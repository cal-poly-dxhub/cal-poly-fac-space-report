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

`cdk deploy` ends by printing its outputs. Three of them are complete commands with
the region, pool id and secret already filled in. Paste each one and replace only
the words in capitals.

- `LoginStep1CreateUser` then `LoginStep2SetPassword` make a sign-in. Replace `EMAIL`
  (both places in step 1) and `PASSWORD`. The account works at once: no emailed
  temporary password, no forced change. Passwords need 12 characters with upper
  case, lower case, a digit and a symbol. Nobody can sign themselves up, so this is
  the only way in.
- `PlanonPutLogin` stores the Planon login the pull uses. Replace `PLANON_USER` and
  `PLANON_PASSWORD`.

Both passwords pass through your shell history. Clear it, or have the owner change
theirs with "Forgot your password?" on the sign-in page.

Sign-in is email and password only. MFA is off for the first deploy; the comment
next to `mfa=` in `fac_space_report/stack.py` shows the two lines that turn it on.
That change updates the pool in place, and Cognito's sign-in pages then walk each
user through setting up an authenticator app.

To remove an account:

    aws cognito-idp admin-delete-user --user-pool-id <UserPoolId> --username EMAIL

## What the page needs

The static page is not in this repo yet. When it is, upload it to `<SiteBucketName>`
and invalidate `/*` on the distribution. It needs four stack outputs: `LoginUrl` and
`SiteClientId` to send the browser to sign in (authorization code flow with PKCE),
`SiteUrl` as the redirect address, and `ApiEndpoint...` for the calls below. Every
call sends the ID token in the `Authorization` header.

| Call | Does | Answers |
| --- | --- | --- |
| `POST /refresh` | starts a pull | `202 {"started": true}` at once |
| `GET /refresh` | the poll | `{"finished_at": null}` while a pull is running, then the marker. A good pull: `{"status": "complete", "finished_at": "...", "rows": {...}}`. A failed one: `{"status": "failed", "finished_at": "...", "error": "..."}` |
| `GET /generate?ref_date=YYYY-MM-DD` | builds the report | `200` with `report.csv` as the body, `400` for a bad date, `409` if nothing has been pulled yet |

`POST /refresh` deletes the old marker before it does anything else, so any marker
the poll sees belongs to the pull just started.

Three things the stack cannot enforce, all from AWS's Cognito guidance:

- Cognito treats PKCE as optional, so the page has to send it. `S256` is the only
  method Cognito accepts, with a fresh verifier for every sign-in.
- Keep tokens in memory. AWS: "Don't store ID and access tokens in local storage."
- The sign-in session on Cognito's side lasts one hour and token refresh does not
  extend it, so expect to send the owner back through sign-in after that.
