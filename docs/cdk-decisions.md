# CDK decision log

`aws-deployment.drawio` is the source of truth for what gets built. This file
records every choice the diagram did not make, and why. One section per piece,
in the order the pieces were built.

## 1. App scaffold

- **App lives in `cdk/`, one stack, one file (`cdk/fac_space_report/stack.py`).**
  The diagram is a single "AWS account" box with seven components. Splitting that
  across stacks or constructs adds indirection for whoever inherits it and buys nothing.
- **Own dependency file, `cdk/requirements.txt`.** Required by the brief. The
  pipeline and connector requirements are untouched.
- **`aws-cdk-lib` pinned exactly (`==2.270.0`, current on 2026-09-21), `constructs`
  ranged.** An exact pin means the same template synthesizes next year as today.
  Upgrading is a deliberate one-line change, not something that happens on
  `pip install`. `constructs` keeps the range `aws-cdk-lib` itself declares.
- **`cdk.json` is `cdk init` output (CLI 2.1129.0), kept verbatim, plus the two
  flags that CLI's template lags behind the library on:**
  `@aws-cdk/aws-ecs:removeEmptyLoadBalancers` (irrelevant here, no ECS) and
  `@aws-cdk/core:validateAgainstDefaultRules`. Result matches the library's own
  `recommended-feature-flags.json` exactly. New apps should start on current
  defaults; a trimmed list silently means older behavior.
- **`@aws-cdk/core:validateAgainstDefaultRules` is `true`.** It turns
  CloudFormation validation findings from warnings into synth failures, which
  makes "clean synth" a stronger claim. Checked that it bites: a scratch stack
  with a bogus S3 property failed synth with exit code 1.
- **No `env` on the stack (account- and region-agnostic).** The campus account and
  region are not in the diagram and the target is synth, not deploy. `app.py`
  marks the one line to change.
- **One tag, `Project=fac-space-report`, set as a stack tag.** So campus staff can
  find and cost-allocate these resources. Set with `tags=` on the stack, not
  `Tags.of()`: with `explicitStackTags` on, `Tags.of()` never reaches the stack.
  CloudFormation copies stack tags to every resource that supports them.
- **`cdk/README.md` with the four setup commands.** Whoever inherits this should
  be able to synth without reading CDK docs first.
- **`cdk/.gitignore` rather than editing the root one.** Keeps the CDK app
  self-contained. Ignores `cdk.out/` and the venv.

## 2. Static site (S3 + CloudFront)

- **CloudFront in front of a private bucket. This departs from the diagram, which
  shows S3 alone.** Decided by Kyle: follow the AWS standard. S3 website endpoints
  are HTTP only, and browser sign-in code needs `crypto.subtle`, which exists only
  on HTTPS pages. The diagram should get a CloudFront icon to match.
- **Origin access control, not origin access identity.** OAI is the legacy
  mechanism; OAC is what AWS documents for new distributions. The bucket policy
  CDK generates allows only this one distribution.
- **Bucket: all public access blocked, SSE-S3, TLS-only policy.** SSE-S3 rather
  than KMS because OAC with KMS needs a key policy and the page is not sensitive.
- **No custom domain or certificate.** The diagram names none, so the site is on
  its `*.cloudfront.net` name. Side effect: CloudFront fixes the minimum TLS
  version for its default certificate, so it cannot be raised until a domain is added.
- **`PriceClass_100`.** Users are on campus in California. The cheapest class
  covers North America.
- **AWS managed `SecurityHeadersPolicy` and default `CachingOptimized`.** Managed
  policies over hand-rolled ones. The page only changes when someone uploads it,
  so invalidate `/*` after an upload.
- **No access logging, no WAF.** One-owner internal tool; each adds a bucket or a
  monthly charge that is not in the diagram. Revisit if campus security asks
  (Security Hub controls CloudFront.5 and CloudFront.6).
- **Buckets keep CDK's default `RETAIN`.** `cdk destroy` leaves the bucket behind.
  The alternative, `auto_delete_objects`, adds a custom-resource Lambda that is not
  in the diagram.
- **The stack does not upload the page.** The page does not exist yet, and
  `BucketDeployment` adds another custom-resource Lambda. Upload with
  `aws s3 sync`; the `SiteBucketName` and `SiteUrl` outputs are there for that.

## 3. Data bucket

- **Fully private: all public access blocked, SSE-S3, TLS-only policy, no CORS.**
  Nothing outside the account reads it directly. How the browser's step 4 poll
  reaches the marker without making anything public is decided in section 8.
- **SSE-S3, not a KMS key.** Building names and square footage are not regulated
  data. A customer key adds a monthly charge and a key policy to maintain. One
  line to change if campus security wants KMS.
- **No versioning, no lifecycle rules.** The diagram says "current copy, replaced
  by each pull", and Planon stays the system of record, so a bad copy is fixed by
  pulling again. Overwritten objects leave nothing to expire.
- **Key layout: `tables/<Table>.csv` and `marker/complete.json`.** Constants at
  the top of `stack.py`, handed to the Lambdas as environment variables so the
  layout is written down once. The marker is JSON so it can carry the pull's
  finish time, which the page needs to tell a new marker from the previous one.
- **Default `RETAIN`, same reasoning as the site bucket.**

## 4. Planon credentials secret

- **The stack creates the secret holding a placeholder; the real login is entered
  by hand after deploy** (command in `cdk/README.md`). The alternative,
  `secret_string_value`, writes the plaintext into `cdk.out/` and the
  CloudFormation console.
- **Placeholder is JSON, `{"username": "REPLACE_ME", "password": <random>}`.** It
  shows whoever fills it in the exact shape the pull Lambda reads. The two keys
  mirror the connector's `PLANON_USERNAME` and `PLANON_PASSWORD`.
- **Do not edit the placeholder template after go-live.** A change to
  `GenerateSecretString` makes CloudFormation generate a fresh value, which would
  overwrite the real login. Not tested here, since nothing is deployed.
- **No fixed `secret_name`.** A deleted secret holds its name for the recovery
  window, so a fixed name can block a redeploy. The generated name still starts
  with `PlanonCredentials`, and `PlanonSecretName` is a stack output.
- **No rotation.** Planon issues and changes this password, not AWS, so there is
  nothing for a rotation Lambda to call. Security Hub's SecretsManager.1 will flag
  it; that is a known, accepted finding.
- **Default AWS managed key and default removal policy (`Delete`).** Re-entering
  two values after a rebuild is cheaper than an orphaned secret.

## 5. pull Lambda

- **The handler is real, thin glue in `cdk/functions/pull/handler.py`.** Synth
  needs code to package, and a stub would leave diagram step 3 unbuilt. It imports
  `connector/planon_odata.py` unchanged. Checked against fakes only, never
  against Planon or AWS.
- **Packaging is CDK's documented Docker bundling (`bundle()` in `stack.py`).** The
  connector needs `requests`, which the Lambda runtime does not ship, so something
  has to run pip. Cost: synth needs Docker running. The alpha `PythonFunction`
  construct does the same job but is experimental, a bad fit for a handoff.
- **The repo root is mounted, and the asset hash is taken from the output.** The
  zip combines files from `cdk/` and `connector/`. Hashing the source would drag
  in `.git` and `.venv`; hashing one handler folder would miss connector changes.
- **Bundling platform pinned to the function's architecture.** `requests` pulls in
  `charset_normalizer`, which has compiled `.so` files. Without the pin, an x86
  laptop would build x86 binaries into an ARM function and it would still synth.
- **Own `requirements.txt` with `requests==2.34.2`, not the connector's.** The
  connector's file also lists `boto3`, which the runtime already has and which
  would add roughly 100 MB. Transitive packages float, so `certifi`'s CA bundle
  stays current on each build. `requests` now has two version specs to keep in step.
- **Python 3.13 on arm64.** 3.13 is the version the pipeline has been run and
  compared against Planon on locally; 3.14 exists but buys nothing here. arm64 is
  cheaper per millisecond and AWS's default recommendation for new functions.
- **512 MB, 15 minute timeout.** The connector holds a whole table in memory to
  build its CSV header. Nobody has timed a full pull, the invoke is asynchronous
  so no one waits on it, and billing is for time used. Tune down once measured.
- **No async retries (`retry_attempts=0`).** Lambda's default of two silent retries
  means three logins with a bad password. The owner clicks Refresh again instead.
- **No reserved concurrency.** It would stop two overlapping pulls, but it fails to
  deploy in new accounts with a low concurrency quota. Overlapping pulls write the
  same data to the same keys, so the damage is nil.
- **Marker is deleted first and written last; failures write nothing.** A poll can
  then never mistake the previous pull for this one. A failed pull shows up as a
  marker that never arrives, plus the error in the log group.
- **Explicit log group, 13 months retention, default `RETAIN`.** The report is
  annual, so 13 months keeps last year's run visible for comparison.
  `log_retention` on the function is deprecated in favour of `log_group`.
- **Permissions via L2 grants: read this one secret; write `tables/*` and the
  marker key.** `grant_write` includes a few tagging and legal-hold actions the
  handler does not use. Kept for readability; they do nothing on this bucket.
- **No VPC, no X-Ray. This assumes `planon.calpoly.edu` answers from the public
  internet.** If it is campus-only, this function needs a VPC with a route to
  campus, and that is a diagram change. **Unverified. Check before deploying.**
- **Planon base URL is not configured here.** The connector's default is the Cal
  Poly endpoint; repeating it would make two places to change.

## 6. build Lambda

- **Real, thin handler in `cdk/functions/build/handler.py`, calling
  `pipeline/build_report.py` unchanged through its own `main()`.** It downloads the
  five tables into the folder layout `LocalSource` already reads, so the pipeline
  needed no S3 code. Checked against a made-up dataset with hand-checkable sums;
  never against real Planon data, which is not in the repo.
- **Speaks API Gateway's Lambda proxy format.** That is what the L2
  `LambdaIntegration` uses by default, and it lets the function set
  `Content-Type: text/csv` and the download filename itself. The report goes back
  in the response body, as the diagram says; it is never written to S3.
- **The reference date arrives as `?ref_date=YYYY-MM-DD`.** Generating a report
  reads data and changes nothing, so it is a GET. The handler rejects anything
  that is not a real date with 400, and normalises it, because the pipeline
  compares dates as strings.
- **409 with "Run Refresh first" when the tables are missing.** Otherwise a fresh
  deploy's first click returns a bare 500.
- **The function sets the CORS headers, and gets the site's origin as
  `SITE_ORIGIN`.** The page (CloudFront) and the API are different origins, and
  with a proxy integration only the function can add response headers. The
  allowed origin is the one site, not `*`.
- **1024 MB, 29 second timeout.** Someone is waiting on this one, and Lambda CPU
  scales with memory. API Gateway drops an integration at 29 seconds, so a longer
  timeout could never deliver a result.
- **Same Docker bundling as pull, though it only copies two files.** One packaging
  mechanism to understand. The pipeline is standard library only, so no pip step.
- **Same runtime, architecture and log retention as pull.** Read-only grant on
  `tables/*`; the L2 grant also allows listing the bucket, which is harmless.
