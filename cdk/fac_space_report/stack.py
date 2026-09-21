from pathlib import Path

from aws_cdk import AssetHashType, Aws, BundlingOptions, CfnOutput, Duration, Stack
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME = lambda_.Runtime.PYTHON_3_13
ARCHITECTURE = lambda_.Architecture.ARM_64

# Layout of the data bucket, shared by both Lambdas and the API.
TABLES_PREFIX = "tables/"
MARKER_KEY = "marker/complete.json"


def bundle(*commands: str) -> lambda_.Code:
    """Build a Lambda zip in the runtime's Docker image, with the repo at /asset-input.

    The handlers reuse connector/ and pipeline/ as they are, so the whole repo is
    mounted rather than one handler directory. Hashing the output, not the source,
    keeps .git and .venv out of the asset hash.
    """
    return lambda_.Code.from_asset(
        str(REPO_ROOT),
        asset_hash_type=AssetHashType.OUTPUT,
        bundling=BundlingOptions(
            image=RUNTIME.bundling_image,
            # pip picks compiled wheels for the container it runs in, so the container
            # has to be the Lambda's architecture, whatever the laptop is.
            platform=ARCHITECTURE.docker_platform,
            command=["bash", "-c", " && ".join(commands)],
        ),
    )


class FacSpaceReportStack(Stack):
    """Everything inside the "AWS account" box of docs/aws-deployment.drawio."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Both buckets keep replaced objects for 90 days, so a bad upload or a bad
        # pull can be rolled back, then let them expire.
        keep_old_versions = s3.LifecycleRule(
            noncurrent_version_expiration=Duration.days(90),
            abort_incomplete_multipart_upload_after=Duration.days(7),
        )

        # "S3 static site". The bucket is private; CloudFront is the only way in.
        site_bucket = s3.Bucket(
            self,
            "SiteBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=True,
            lifecycle_rules=[keep_old_versions],
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
        self.site_url = f"https://{site.distribution_domain_name}"

        # "S3 data bucket": the current copy of the five tables plus the completion
        # marker. Each pull replaces the last; the current version is the current copy.
        data_bucket = s3.Bucket(
            self,
            "DataBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=True,
            lifecycle_rules=[keep_old_versions],
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
            code=bundle(
                "pip install -r cdk/functions/pull/requirements.txt -t /asset-output --no-compile --no-cache-dir",
                "cp cdk/functions/pull/handler.py connector/planon_odata.py /asset-output",
            ),
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
            code=bundle(
                "pip install -r cdk/functions/build/requirements.txt -t /asset-output --no-compile --no-cache-dir",
                "cp cdk/functions/build/handler.py pipeline/build_report.py /asset-output",
            ),
            memory_size=1024,
            # API Gateway gives up on an integration after 29 seconds.
            timeout=Duration.seconds(29),
            log_group=logs.LogGroup(
                self, "BuildLogs", retention=logs.RetentionDays.THIRTEEN_MONTHS
            ),
            environment={
                "DATA_BUCKET": data_bucket.bucket_name,
                "TABLES_PREFIX": TABLES_PREFIX,
                "SITE_ORIGIN": self.site_url,
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
            ),
            mfa=cognito.Mfa.OPTIONAL,
            mfa_second_factor=cognito.MfaSecondFactor(sms=False, otp=True),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            feature_plan=cognito.FeaturePlan.ESSENTIALS,
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
                callback_urls=[self.site_url, f"{self.site_url}/"],
                logout_urls=[self.site_url, f"{self.site_url}/"],
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

        CfnOutput(self, "SiteUrl", value=self.site_url)
        CfnOutput(self, "SiteBucketName", value=site_bucket.bucket_name)
        CfnOutput(self, "DataBucketName", value=data_bucket.bucket_name)
        CfnOutput(self, "PlanonSecretName", value=planon_secret.secret_name)
        CfnOutput(self, "UserPoolId", value=user_pool.user_pool_id)
        CfnOutput(self, "SiteClientId", value=site_client.user_pool_client_id)
        CfnOutput(self, "LoginUrl", value=login_domain.base_url())
