# CDK app

Builds the deployment drawn in `../docs/aws-deployment.drawio`. Every choice the
diagram did not make is written down in `../docs/cdk-decisions.md`.

Needs Python 3.10+, Node 20+, the CDK CLI (`npm install -g aws-cdk`), and Docker
running. Synth builds the Lambda zips inside AWS's Lambda build image, so the
first run downloads that image and takes a minute.

    cd cdk
    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    cdk synth

`cdk synth` prints the CloudFormation template and writes it to `cdk.out/`. It
does not touch AWS and needs no credentials.

## Before the first deploy

The API writes access and error logs to CloudWatch. API Gateway can only do that
if the account has a logging role set for the region, which is an account-wide
setting this stack deliberately does not touch. Check it:

    aws apigateway get-account --query cloudwatchRoleArn

If that prints `None`, the deploy fails at the API stage with "CloudWatch Logs role
ARN must be set in account settings". Set it once per region, following "Permissions
for CloudWatch logging" in the API Gateway developer guide.

## After the first deploy

Two one-time steps. The names in angle brackets are stack outputs.

The stack creates the Planon secret holding a placeholder. Put the real login in it.

    aws secretsmanager put-secret-value \
      --secret-id <PlanonSecretName> \
      --secret-string '{"username": "...", "password": "..."}'

Nobody can sign themselves up. Create the report owner's account; Cognito emails
them a temporary password. At first sign-in it makes them choose a new one and set
up an authenticator app, which it then asks for every time.

    aws cognito-idp admin-create-user \
      --user-pool-id <UserPoolId> \
      --username owner@calpoly.edu \
      --user-attributes Name=email,Value=owner@calpoly.edu Name=email_verified,Value=true

If the owner loses their authenticator, delete the account and create it again.
Accounts hold no data, so nothing is lost.

    aws cognito-idp admin-delete-user --user-pool-id <UserPoolId> --username owner@calpoly.edu

## What the page needs

The static page is not in this repo yet. When it is, upload it to `<SiteBucketName>`
and invalidate `/*` on the distribution. It needs four stack outputs: `LoginUrl` and
`SiteClientId` to send the browser to sign in (authorization code flow with PKCE),
`SiteUrl` as the redirect address, and `ApiEndpoint...` for the calls below. Every
call sends the ID token in the `Authorization` header.

| Call | Does | Answers |
| --- | --- | --- |
| `POST /refresh` | starts a pull | `202 {"started": true}` at once |
| `GET /refresh` | the poll | `{"finished_at": null}` until the pull is done, then the marker: `{"finished_at": "...", "rows": {...}}` |
| `GET /generate?ref_date=YYYY-MM-DD` | builds the report | `200` with `report.csv` as the body, `400` for a bad date, `409` if nothing has been pulled yet |

To tell a new pull from the last one, compare `finished_at` with when Refresh was clicked.
