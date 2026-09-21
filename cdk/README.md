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

## After the first deploy

The stack creates the Planon secret holding a placeholder. Put the real login in
it once. `PlanonSecretName` is in the stack outputs.

    aws secretsmanager put-secret-value \
      --secret-id <PlanonSecretName> \
      --secret-string '{"username": "...", "password": "..."}'
