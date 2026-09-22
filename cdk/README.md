# CDK app

Needs Python 3.10+, Node 20+, the CDK CLI (`npm install -g aws-cdk`), and Docker
running. Synth builds the Lambda zips inside AWS's Lambda build image, so the
first run downloads that image and takes a minute.

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

To remove an account:

    aws cognito-idp admin-delete-user --user-pool-id <UserPoolId> --username EMAIL
