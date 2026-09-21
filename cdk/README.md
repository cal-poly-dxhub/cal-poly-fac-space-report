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

## Deploying

With AWS credentials for the target account in your shell, and Docker running:

    cdk bootstrap     # once per account and region, before the first deploy
    cdk deploy

The stack goes to whatever account and region those credentials default to. The
first deploy takes around ten minutes, most of it CloudFront. Skipping `bootstrap`
fails the deploy with a message about a missing toolkit stack or SSM parameter.

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

To remove an account:

    aws cognito-idp admin-delete-user --user-pool-id <UserPoolId> --username EMAIL

## Before real users

Two switches at the top of `fac_space_report/stack.py` are off so that a first
deploy needs nothing set up beforehand. Set each to `True` and redeploy. Both
update in place.

- `REQUIRE_MFA`. Until then sign-in is email and password only. Once on, Cognito's
  sign-in pages walk each user through setting up an authenticator app.
- `API_LOGGING`. The record of who called the API, plus API Gateway's own error log.
  API Gateway can only write logs if the account has a logging role set for the
  region, an account-wide setting this stack deliberately does not touch. Check:

      aws apigateway get-account --query cloudwatchRoleArn

  If that prints `None`, set it first, following "Permissions for CloudWatch
  logging" in the API Gateway developer guide. Otherwise the deploy fails at the
  API stage with "CloudWatch Logs role ARN must be set in account settings".

The two Lambda log groups are always on and need no setup. A failed pull or build
explains itself there.

## Testing a deploy

1. `cdk deploy`. It uploads the page and writes its `config.json`, so there is
   nothing to copy by hand.
2. Paste the three printed commands (see "After the first deploy").
3. Open `SiteUrl` and sign in.
4. Refresh from Planon. The status line counts the elapsed time, and when it
   finishes the panel shows how many rows each table has. If it fails, the reason
   is shown in the same place, for example a 401 from Planon.
5. Pick a reference date and generate the report. It appears as a grid, with the
   center bands and subtotals of Planon's own export, and Download CSV saves the
   exact bytes the API returned.

"Deployment details" under Planon data shows which API, sign-in domain and app
client the page is talking to, who is signed in, and when the session ends.

## The page

`site/` is plain HTML, CSS and JavaScript with no build step. It uses Cal Poly's
colors and Source Sans, the body typeface on calpoly.edu, served from `site/fonts/`
under its open license. Nothing is loaded from a third party.

Sign-in is the authorization code flow with PKCE against Cognito's pages. Tokens
live in memory only, so a reload signs in again, silently while Cognito's own
one-hour session lasts. Every API call sends the ID token in `Authorization`.

| Call | Does | Answers |
| --- | --- | --- |
| `POST /refresh` | starts a pull | `202 {"started": true}` at once |
| `GET /refresh` | the poll | `{"finished_at": null}` while a pull is running, then the marker. A good pull: `{"status": "complete", "finished_at": "...", "rows": {...}}`. A failed one: `{"status": "failed", "finished_at": "...", "error": "..."}` |
| `GET /generate?ref_date=YYYY-MM-DD` | builds the report | `200` with `report.csv` as the body, `400` for a bad date, `409` if nothing has been pulled yet |

`POST /refresh` deletes the old marker before it does anything else, so any marker
the poll sees belongs to the pull just started.
