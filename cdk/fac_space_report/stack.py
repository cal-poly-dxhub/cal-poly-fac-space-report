import json
from pathlib import Path

from aws_cdk import AssetHashType, Aws, BundlingOptions, CfnOutput, DockerVolume, Duration, Stack
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME = lambda_.Runtime.PYTHON_3_13
ARCHITECTURE = lambda_.Architecture.ARM_64

# Layout of the data bucket, shared by both Lambdas and the API.
TABLES_PREFIX = "tables/"
MARKER_KEY = "marker/complete.json"


def bundle(function: str, reused_module: str) -> lambda_.Code:
    """Build a Lambda zip in the runtime's Docker image.

    The zip holds the handler from cdk/functions/<function>/, its pinned requirements,
    and one module reused as-is from the rest of the repo. The container is shown that
    folder and that one file, nothing else, so pip never runs next to connector/.env
    or source_data/. The asset hash comes from the output, because the reused module
    lives outside the asset's own folder.
    """
    module = REPO_ROOT / reused_module
    return lambda_.Code.from_asset(
        str(REPO_ROOT / "cdk" / "functions" / function),
        asset_hash_type=AssetHashType.OUTPUT,
        bundling=BundlingOptions(
            image=RUNTIME.bundling_image,
            # pip picks compiled wheels for the container it runs in, so the container
            # has to be the Lambda's architecture, whatever the laptop is.
            platform=ARCHITECTURE.docker_platform,
            volumes=[DockerVolume(host_path=str(module), container_path=f"/reused/{module.name}")],
            command=[
                "bash",
                "-c",
                # Wheels only: nothing from PyPI gets to run code during the build.
                "pip install -r requirements.txt -t /asset-output"
                " --only-binary=:all: --no-compile --no-cache-dir"
                f" && cp handler.py /reused/{module.name} /asset-output",
            ],
        ),
    )


class FacSpaceReportStack(Stack):
    """Everything inside the "AWS account" box of docs/aws-deployment.drawio.

    require_mfa and api_logging come from config.yaml. Both default off so a first
    deploy needs nothing set up in the account beforehand.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        require_mfa: bool = False,
        api_logging: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # "S3 static site". The bucket is private; CloudFront is the only way in.
        site_bucket = s3.Bucket(
            self,
            "SiteBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
        )
        site = cloudfront.Distribution(
            self,
            "SiteDistribution",
            comment="fac-space-report static site",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                response_headers_policy=cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
            ),
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            # S3 answers a missing key with 403 here, because CloudFront may not list
            # the bucket. Show the page instead of a raw XML error.
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                )
            ],
        )
        site_url = f"https://{site.distribution_domain_name}"

        # "S3 data bucket": the current copy of the five tables plus the completion
        # marker. Each pull overwrites the last.
        data_bucket = s3.Bucket(
            self,
            "DataBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
        )

        # "Secrets Manager, Planon credentials". Created holding a placeholder in the
        # right shape; the real username and password are entered after deploy and
        # never appear in this repo or the template.
        planon_secret = secretsmanager.Secret(
            self,
            "PlanonCredentials",
            description="Planon datalake OData login used by the pull Lambda",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"username": "REPLACE_ME"}',
                generate_string_key="password",
            ),
        )

        # "pull Lambda". Invoked asynchronously by the Refresh endpoint: reads the
        # Planon login, pulls the five tables over OData, writes them and then the
        # completion marker. Planon is outside AWS, so there is nothing to build for it.
        pull_fn = lambda_.Function(
            self,
            "PullFunction",
            description="Copies the five Planon tables into the data bucket",
            runtime=RUNTIME,
            architecture=ARCHITECTURE,
            handler="handler.handler",
            code=bundle("pull", "connector/planon_odata.py"),
            memory_size=512,
            timeout=Duration.minutes(15),
            # A failed pull is retried by clicking Refresh again, not behind the owner's back.
            retry_attempts=0,
            log_group=logs.LogGroup(
                self, "PullLogs", retention=logs.RetentionDays.THIRTEEN_MONTHS
            ),
            environment={
                "DATA_BUCKET": data_bucket.bucket_name,
                "TABLES_PREFIX": TABLES_PREFIX,
                "MARKER_KEY": MARKER_KEY,
                "PLANON_SECRET_ARN": planon_secret.secret_arn,
            },
        )
        planon_secret.grant_read(pull_fn)
        data_bucket.grant_write(pull_fn, f"{TABLES_PREFIX}*")
        data_bucket.grant_write(pull_fn, MARKER_KEY)

        # "build Lambda". Invoked synchronously by the Generate endpoint with the
        # reference date: reads the tables, returns report.csv in the response.
        build_fn = lambda_.Function(
            self,
            "BuildFunction",
            description="Builds report.csv from the tables in the data bucket",
            runtime=RUNTIME,
            architecture=ARCHITECTURE,
            handler="handler.handler",
            code=bundle("build", "pipeline/build_report.py"),
            memory_size=1024,
            # API Gateway gives up on an integration after 29 seconds by default.
            timeout=Duration.seconds(29),
            log_group=logs.LogGroup(
                self, "BuildLogs", retention=logs.RetentionDays.THIRTEEN_MONTHS
            ),
            environment={
                "DATA_BUCKET": data_bucket.bucket_name,
                "TABLES_PREFIX": TABLES_PREFIX,
                "SITE_ORIGIN": site_url,
            },
        )
        data_bucket.grant_read(build_fn, f"{TABLES_PREFIX}*")

        # "Cognito user pool (standalone)": its own user directory, no campus SSO.
        # Nobody can sign themselves up; an admin creates the owner's account.
        user_pool = cognito.UserPool(
            self,
            "UserPool",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True),
            sign_in_case_sensitive=False,
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
                # The invitation email below states this.
                temp_password_validity=Duration.days(7),
            ),
            # When on: authenticator app only, and managed login walks users through setup.
            mfa=cognito.Mfa.REQUIRED if require_mfa else cognito.Mfa.OFF,
            mfa_second_factor=cognito.MfaSecondFactor(sms=False, otp=True) if require_mfa else None,
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            # Sent when an admin creates an account. The password in it is temporary:
            # managed login makes the user replace it at first sign-in, so the admin
            # never knows the password that stays. Cognito requires both placeholders.
            user_invitation=cognito.UserInvitationConfig(
                email_subject="Your sign-in for the facility space report",
                email_body=(
                    f"You have been given a sign-in for the facility space report at {site_url}<br><br>"
                    "Email: {username}<br>Temporary password: {####}<br><br>"
                    "You will be asked to choose your own password when you first sign in. "
                    "The temporary password expires in 7 days."
                ),
            ),
            feature_plan=cognito.FeaturePlan.ESSENTIALS,
            deletion_protection=True,
        )
        # Sign-in happens on Cognito's own managed login pages, so the site never
        # handles a password. The page sends the browser there and gets a code back.
        login_domain = user_pool.add_domain(
            "LoginDomain",
            cognito_domain=cognito.CognitoDomainOptions(
                # Must be unique in the region; the account id makes it so.
                domain_prefix=f"fac-space-report-{Aws.ACCOUNT_ID}",
            ),
            managed_login_version=cognito.ManagedLoginVersion.NEWER_MANAGED_LOGIN,
        )
        site_client = user_pool.add_client(
            "SiteClient",
            generate_secret=False,
            prevent_user_existence_errors=True,
            supported_identity_providers=[
                cognito.UserPoolClientIdentityProvider.COGNITO
            ],
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL],
                callback_urls=[site_url, f"{site_url}/"],
                logout_urls=[site_url, f"{site_url}/"],
            ),
            refresh_token_validity=Duration.days(1),
        )
        # Managed login shows an error page until the client has a branding style.
        # No L2 for this yet. This one takes Cognito's default look.
        cognito.CfnManagedLoginBranding(
            self,
            "LoginBranding",
            user_pool_id=user_pool.user_pool_id,
            client_id=site_client.user_pool_client_id,
            use_cognito_provided_values=True,
        )

        # API Gateway's own logs: who called what (access log, with the signed-in email)
        # and integration failures (execution log, errors only, payloads never logged).
        # The two non-Lambda integrations below have no other logs.
        # Needs the account's API Gateway CloudWatch role, see docs/install.md.
        logging_options = {}
        if api_logging:
            logging_options = dict(
                logging_level=apigw.MethodLoggingLevel.ERROR,
                access_log_destination=apigw.LogGroupLogDestination(
                    logs.LogGroup(
                        self, "ApiAccessLogs", retention=logs.RetentionDays.THIRTEEN_MONTHS
                    )
                ),
                access_log_format=apigw.AccessLogFormat.custom(
                    json.dumps(
                        {
                            "requestTime": apigw.AccessLogField.context_request_time(),
                            "requestId": apigw.AccessLogField.context_request_id(),
                            "user": apigw.AccessLogField.context_authorizer_claims("email"),
                            "ip": apigw.AccessLogField.context_identity_source_ip(),
                            "method": apigw.AccessLogField.context_http_method(),
                            "path": apigw.AccessLogField.context_resource_path(),
                            "status": apigw.AccessLogField.context_status(),
                        }
                    )
                ),
            )

        # "API Gateway REST API". Every method requires a Cognito token; that is set
        # once as the default so a new method cannot be added open by mistake.
        cors_origin = {"method.response.header.Access-Control-Allow-Origin": f"'{site_url}'"}
        api = apigw.RestApi(
            self,
            "Api",
            rest_api_name="fac-space-report",
            endpoint_types=[apigw.EndpointType.REGIONAL],
            default_method_options=apigw.MethodOptions(
                authorization_type=apigw.AuthorizationType.COGNITO,
                authorizer=apigw.CognitoUserPoolsAuthorizer(
                    self, "Authorizer", cognito_user_pools=[user_pool]
                ),
            ),
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=[site_url],
                allow_methods=["GET", "POST"],
                allow_headers=["Authorization", "Content-Type"],
            ),
            deploy_options=apigw.StageOptions(
                stage_name="prod",
                # One owner, and every Refresh is a full Planon pull.
                throttling_rate_limit=5,
                throttling_burst_limit=10,
                **logging_options,
            ),
        )
        # Without these, a rejected request (expired token, throttled) reaches the
        # page as an unreadable CORS failure instead of a 401 or 429.
        for name, response_type in [
            ("Default4xx", apigw.ResponseType.DEFAULT_4_XX),
            ("Default5xx", apigw.ResponseType.DEFAULT_5_XX),
        ]:
            api.add_gateway_response(
                name,
                type=response_type,
                response_headers={"Access-Control-Allow-Origin": f"'{site_url}'"},
            )

        # "Refresh endpoint", step 2: POST starts a pull and returns at once. The
        # Event invocation type is what makes the invoke asynchronous.
        refresh = api.root.add_resource("refresh")
        refresh.add_method(
            "POST",
            apigw.LambdaIntegration(
                pull_fn,
                proxy=False,
                request_parameters={
                    "integration.request.header.X-Amz-Invocation-Type": "'Event'"
                },
                request_templates={"application/json": "{}"},
                passthrough_behavior=apigw.PassthroughBehavior.NEVER,
                integration_responses=[
                    apigw.IntegrationResponse(
                        status_code="202",
                        response_parameters=cors_origin,
                        response_templates={"application/json": '{"started": true}'},
                    )
                ],
            ),
            method_responses=[
                apigw.MethodResponse(status_code="202", response_parameters={k: True for k in cors_origin})
            ],
        )

        # Step 4, the poll. The diagram has the browser read the marker from S3, but
        # the browser holds a Cognito token, not AWS credentials, and the bucket stays
        # private. So the read goes through the API: GET returns 200 with the marker
        # object, or 202 with {"finished_at": null} while there is none.
        marker_reader = iam.Role(
            self,
            "MarkerReaderRole",
            assumed_by=iam.ServicePrincipal("apigateway.amazonaws.com"),
            description="Lets API Gateway read the completion marker, and nothing else",
        )
        # The L2 grant includes ListBucket, which makes S3 say 404 rather than 403
        # for a missing marker.
        data_bucket.grant_read(marker_reader, MARKER_KEY)
        refresh.add_method(
            "GET",
            apigw.AwsIntegration(
                service="s3",
                integration_http_method="GET",
                path=f"{data_bucket.bucket_name}/{MARKER_KEY}",
                options=apigw.IntegrationOptions(
                    credentials_role=marker_reader,
                    integration_responses=[
                        apigw.IntegrationResponse(status_code="200", response_parameters=cors_origin),
                        # API Gateway keeps one mapping per status code, so "no marker yet"
                        # cannot also be 200. A second 200 silently replaces the first.
                        apigw.IntegrationResponse(
                            status_code="202",
                            selection_pattern="404",
                            response_parameters=cors_origin,
                            response_templates={"application/json": '{"finished_at": null}'},
                        ),
                        # Any other S3 error would otherwise fall through to the 200 above.
                        apigw.IntegrationResponse(
                            status_code="502",
                            selection_pattern=r"(?!404)[45]\d{2}",
                            response_parameters=cors_origin,
                            response_templates={"application/json": '{"error": "could not read the marker"}'},
                        ),
                    ],
                ),
            ),
            method_responses=[
                apigw.MethodResponse(status_code=code, response_parameters={k: True for k in cors_origin})
                for code in ("200", "202", "502")
            ],
        )

        # "Generate endpoint", steps 5-6: a plain synchronous proxy invoke. The
        # function reads ?ref_date= and answers with report.csv.
        api.root.add_resource("generate").add_method("GET", apigw.LambdaIntegration(build_fn))

        # The page in cdk/site/, uploaded on every deploy along with a config.json that
        # tells it where the API and the sign-in pages are. Those addresses only exist
        # once the stack is deployed, so CDK fills them in at deploy time.
        s3deploy.BucketDeployment(
            self,
            "SiteContent",
            destination_bucket=site_bucket,
            sources=[
                s3deploy.Source.asset(str(REPO_ROOT / "cdk" / "site")),
                s3deploy.Source.json_data(
                    "config.json",
                    {
                        "apiUrl": api.url,
                        "loginUrl": login_domain.base_url(),
                        "clientId": site_client.user_pool_client_id,
                    },
                ),
            ],
            # CloudFront drops its cached copy on each deploy, and browsers are told
            # to check back rather than trust theirs.
            distribution=site,
            distribution_paths=["/*"],
            cache_control=[s3deploy.CacheControl.no_cache()],
            log_group=logs.LogGroup(self, "SiteContentLogs", retention=logs.RetentionDays.ONE_MONTH),
        )

        CfnOutput(self, "SiteUrl", value=site_url)
        CfnOutput(self, "SiteBucketName", value=site_bucket.bucket_name)
        CfnOutput(self, "DataBucketName", value=data_bucket.bucket_name)
        CfnOutput(self, "PlanonSecretName", value=planon_secret.secret_name)
        CfnOutput(self, "UserPoolId", value=user_pool.user_pool_id)

        # The two one-time jobs after a first deploy, printed ready to paste. No password
        # is ever typed into either command, so none ends up in shell history, and no
        # secret is in the template. The default delivery medium is SMS, hence the
        # literal EMAIL, which is not a placeholder.
        CfnOutput(
            self,
            "LoginCreateUser",
            value=f"aws cognito-idp --region {Aws.REGION} admin-create-user"
            f" --user-pool-id {user_pool.user_pool_id} --username REPLACE_WITH_EMAIL"
            " --user-attributes Name=email,Value=REPLACE_WITH_EMAIL Name=email_verified,Value=true"
            " --desired-delivery-mediums EMAIL",
        )
        # The login is read from a file, planon.json, which the install guide says to
        # delete afterwards. Inline, it would sit in ~/.zsh_history in plain text.
        CfnOutput(
            self,
            "PlanonPutLogin",
            value=f"aws secretsmanager --region {Aws.REGION} put-secret-value"
            f" --secret-id {planon_secret.secret_arn}"
            " --secret-string file://planon.json",
        )
        CfnOutput(self, "SiteClientId", value=site_client.user_pool_client_id)
        CfnOutput(self, "LoginUrl", value=login_domain.base_url())
