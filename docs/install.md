# Install guide

## 1. In your AWS account

- Credentials that can create IAM roles, Lambda, S3, CloudFront, Cognito, API Gateway and
  Secrets Manager.
- Python 3.10+, Node.js 20+, and the CDK CLI (`npm install -g aws-cdk`).
- Docker, running. Synth builds the Lambda zips inside AWS's Lambda build image, so the first
  run downloads that image and takes a minute.
- `cdk bootstrap`, once per account and region.

The stack goes to whatever account and region your credentials default to. Any region with the
services above works.

## 2. Settings

Two switches at the top of `cdk/fac_space_report/stack.py`. Both start off so a first deploy
needs nothing set up in the account beforehand. Turn both on before real users; each updates in
place with another `cdk deploy`.

**`REQUIRE_MFA`** - sign-in also asks for a code from an authenticator app. Cognito's login
page walks each user through setting one up the first time.

**`API_LOGGING`** - API Gateway logs who called what, with the signed-in email, plus any
integration errors. API Gateway can only write logs through a role set once per account and
region. Check whether yours already has one:

```bash
aws apigateway get-account --query cloudwatchRoleArn
```

If that prints an ARN, turn the switch on and deploy. If it prints `null`, create the role
first, or the deploy fails:

```bash
aws iam create-role --role-name APIGatewayCloudWatchLogs \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"apigateway.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam attach-role-policy --role-name APIGatewayCloudWatchLogs \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs
aws apigateway update-account \
  --patch-operations op=replace,path=/cloudwatchRoleArn,value=arn:aws:iam::ACCOUNT_ID:role/APIGatewayCloudWatchLogs
```

That role is shared by every API in the account and region, so it lives outside this stack and
`cdk destroy` leaves it alone.

## 3. Deploy

```bash
cd cdk
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

cdk bootstrap   # once per account and region
cdk deploy
```

Use the venv in `cdk/`. With a different one active, `cdk deploy` fails with
`ModuleNotFoundError: No module named 'aws_cdk'`. Skipping `bootstrap` fails with a message
about a missing toolkit stack or SSM parameter.

The first deploy takes around ten minutes, most of it CloudFront.

## 4. From the outputs

`cdk deploy` ends by printing its outputs. Three of them are complete commands with the region,
pool id and secret already filled in. Paste each one and replace only the words in capitals.
They are also on the stack's **Outputs** tab in the CloudFormation console.

### `PlanonPutLogin`

*Stores the Planon login the pull uses.*

Replace `PLANON_USER` and `PLANON_PASSWORD`. The deploy creates the secret with a placeholder,
so until you run this every Refresh fails. The login never appears in the repo or the template.

### `LoginStep1CreateUser`, then `LoginStep2SetPassword`

*Gives someone a sign-in.*

Replace `EMAIL` (both places in step 1) and `PASSWORD`. The account works at once: no emailed
temporary password, no forced change. Passwords need 12 characters with upper case, lower case,
a digit and a symbol. Nobody can sign themselves up, so this is the only way in.

Another person is the same two commands with another address. To remove one:

```bash
aws cognito-idp admin-delete-user --user-pool-id USER_POOL_ID --username EMAIL
```

`USER_POOL_ID` is the `UserPoolId` output.

### `SiteUrl`

*The page.*

Open it, sign in, and click **Refresh from Planon**. When it finishes the page lists a row
count for each of the five tables: that proves the Planon login works and Planon is reachable
from AWS. Then pick a reference date and click **Generate report**. A report on screen is the
end-to-end check.

If Refresh fails, the page shows the error under **Planon data**. The pull Lambda's full log is
in CloudWatch, in the log group whose name starts with `FacSpaceReportStack-PullLogs`.

## 5. Updating

Change the code, then `cdk deploy` again from `cdk/`. The page is re-uploaded on every deploy
and browsers pick it up on the next load. The Planon login, the sign-ins and the last refresh
all survive a redeploy.

## 6. Tearing down

```bash
cdk destroy
```

Some things are kept on purpose, so a mistaken destroy loses nothing that matters:

- both S3 buckets (the page, and the last copy of the Planon tables)
- the Cognito user pool, which also has deletion protection on
- the three log groups (four with `API_LOGGING` on)

A later deploy does not reuse them; it makes new ones. To find what an old install left
behind, everything the stack made carries the tag `Project=fac-space-report`:

```bash
aws resourcegroupstaggingapi get-resources \
  --tag-filters Key=Project,Values=fac-space-report \
  --query 'ResourceTagMappingList[].ResourceARN'
```

Anything in that list after a destroy is a leftover. To delete them for good:

```bash
aws s3 rb s3://BUCKET_NAME --force
aws cognito-idp update-user-pool --user-pool-id USER_POOL_ID --deletion-protection INACTIVE
aws cognito-idp delete-user-pool --user-pool-id USER_POOL_ID
aws logs delete-log-group --log-group-name LOG_GROUP_NAME
```

Run `update-user-pool` only right before deleting the pool: it resets every pool setting you
don't pass to it.
