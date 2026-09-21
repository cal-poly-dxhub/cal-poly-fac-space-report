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
  mechanism; OAC is what AWS documents for new distributions and what Security Hub
  CloudFront.13 checks for. The bucket policy CDK generates allows only this one
  distribution.
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
- **Unknown paths get the page, not an error.** With OAC, CloudFront cannot list
  the bucket, so S3 answers a missing key with 403 and the visitor sees raw XML.
  One error response maps 403 to `/index.html`. Fine for a one-page site; it would
  hide a missing asset on a bigger one.
- **No access logging, no WAF.** Each adds a bucket or a monthly charge that is not
  in the diagram. See the accepted findings table at the end.
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
- **Versioned, with replaced objects expiring after 90 days. Same on the site
  bucket.** Reverses an earlier "no versioning" call. The current version is still
  the diagram's "current copy, replaced by each pull"; the 90 days let someone
  compare against or restore the previous pull. It is a setting on a bucket the
  diagram already has, and it clears Security Hub S3.14, S3.13 and S3.10.
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
- **Do not edit the placeholder template after go-live.** The CloudFormation
  reference says of `GenerateSecretString`: "When you make a change to this
  property, a new secret version is created." That new version would replace the
  real login.
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
- **The build container sees the handler folder and one reused file, nothing
  else.** The zip combines files from `cdk/` and `connector/`. An earlier version
  mounted the whole repo, which put `connector/.env` (live Planon login) and
  `source_data/` in front of `pip` while it ran. CDK's `exclude` does not help:
  it filters what gets hashed, not what gets mounted. pip also runs with
  `--only-binary=:all:`, so nothing from PyPI executes during the build.
- **The asset hash is taken from the output.** The reused module sits outside the
  asset's own folder, so a source hash would miss changes to it.
- **Bundling platform pinned to the function's architecture.** `requests` pulls in
  `charset_normalizer`, which has compiled `.so` files. Without the pin, an x86
  laptop would build x86 binaries into an ARM function and it would still synth.
- **Own `requirements.txt`, pinned exactly: `requests==2.34.2`, `boto3==1.43.99`.**
  The connector's file uses open ranges, which would make the zip differ from one
  build to the next. `boto3` is packaged even though the runtime has a copy: the
  Lambda docs say to use the runtime's copy "only when you can't include additional
  packages", because it changes without notice. Reverses an earlier call to leave
  it out. Cost is a 29 MB zip against a 250 MB limit. Transitive packages float, so
  `certifi`'s CA bundle stays current. `requests` now has two specs to keep in step.
- **Python 3.13 on arm64.** 3.13 is the version the pipeline has been run and
  compared against Planon on locally. Lambda's 3.14 runtime has the same
  deprecation date (Jun 30, 2029), so it buys no extra life. arm64 because AWS
  documents "significantly better price and performance" and every package here
  has an arm64 build. It is offered in most regions, not all; check the target.
- **512 MB, 15 minute timeout.** The connector holds a whole table in memory to
  build its CSV header. Nobody has timed a full pull, the invoke is asynchronous
  so no one waits on it, and billing is for time used. Tune down once measured.
- **No async retries (`retry_attempts=0`).** Lambda's default of two silent retries
  means three logins with a bad password. The owner clicks Refresh again instead.
- **No reserved concurrency.** It would stop two overlapping pulls, but the Lambda
  quotas page warns that new accounts start with reduced concurrency, and reserving
  from a small pool can fail the deploy. Overlapping pulls write the same data to
  the same keys, so the damage is nil.
- **Marker is deleted first and written last; failures write nothing.** A poll can
  then never mistake the previous pull for this one. A failed pull shows up as a
  marker that never arrives, plus the error in the log group.
- **Explicit log group, 13 months retention, default `RETAIN`.** The report is
  annual, so 13 months keeps last year's run visible for comparison.
  `log_retention` on the function is deprecated in favour of `log_group`.
- **Permissions via L2 grants: read this one secret; write `tables/*` and the
  marker key.** `grant_write` includes a few tagging and legal-hold actions the
  handler does not use. Kept for readability; they do nothing on this bucket.
- **X-Ray active tracing on, on both functions.** Reverses an earlier "no X-Ray".
  One setting each, it clears Security Hub Lambda.7, and a trace is the quickest
  way to see where a slow pull spent its time. Volume is a few hundred traces a year.
- **No VPC. This assumes `planon.calpoly.edu` answers from the public
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
- **The pipeline's exit status is checked.** `main()` only returns 0 or raises
  today. If that ever changes, a non-zero status becomes an error here instead of a
  200 with a stale file.
- **The report has to fit in 6 MB.** That is Lambda's limit on a synchronous
  response, and it binds before API Gateway's 10 MB. Neither can be raised. At 187
  facilities the report is a few tens of KB. If it ever gets close, the design has
  to change to a presigned S3 link, which the diagram does not show.
- **409 with "Run Refresh first" when the tables are missing.** Otherwise a fresh
  deploy's first click returns a bare 500.
- **The function sets the CORS headers, and gets the site's origin as
  `SITE_ORIGIN`.** The page (CloudFront) and the API are different origins, and
  with a proxy integration only the function can add response headers. The
  allowed origin is the one site, not `*`.
- **1024 MB, 29 second timeout.** Someone is waiting on this one, and Lambda CPU
  scales with memory. API Gateway's integration timeout is 29 seconds by default,
  so a longer function timeout could never deliver a result. For Regional APIs that
  limit can now be raised by quota request, if the report ever outgrows it.
- **Same Docker bundling as pull.** One packaging mechanism to understand. The
  pipeline is standard library only; the pip step is there for the pinned `boto3`
  the handler uses, for the reason given in section 5.
- **Same runtime, architecture and log retention as pull.** Read-only grant on
  `tables/*`; the L2 grant also allows listing the bucket, which is harmless.

## 7. Cognito user pool

- **"Standalone" read as: its own user directory, no campus SSO.** Only the
  `COGNITO` provider is enabled. If it was meant as "no hosted sign-in pages",
  the next entry is the one to revisit.
- **Sign-in uses Cognito's managed login pages, authorization code flow.** Now
  that the site is on HTTPS this is the AWS standard for a browser app, and the
  page never touches a password. It also handles temporary passwords, password
  reset and MFA setup, all of which the page would otherwise have to build. Adds
  a login domain and a branding resource the diagram does not draw.
- **Code flow only; implicit flow off.** CDK turns both on by default. Implicit
  puts tokens in the URL and is the one current guidance says to avoid. No client
  secret, since a browser cannot keep one.
- **Domain prefix is `fac-space-report-<account id>`.** Prefixes must be unique
  per region, and the account id guarantees that with nothing to configure.
- **`CfnManagedLoginBranding` is the one L1 in the stack.** Managed login shows an
  error page until the client has a branding style, and CDK has no L2 for it.
  It takes Cognito's default look.
- **No self sign-up. An admin creates accounts** (command in `cdk/README.md`).
  Anyone who can sign in can pull Planon data, so an open sign-up page would be
  the whole perimeter.
- **Email sign-in, case-insensitive; 12 characters with all four character
  classes, written out in full.** Left to defaults, the template omits the
  character-class rules and it is unclear what Cognito then assumes.
- **MFA required, authenticator app only, no SMS.** Reverses an earlier
  "optional". Security Hub Cognito.5 expects MFA on a password pool. The lockout
  worry behind "optional" was weak: managed login walks the user through
  authenticator setup, and an admin fixes a lost phone by recreating the account
  (command in `cdk/README.md`). SMS needs an SNS role and a spend limit. Email
  codes need an SES identity, which is a campus decision.
- **Deletion protection on.** Security Hub Cognito.6. To remove the pool on
  purpose, turn the setting off first.
- **No threat protection (Cognito.1 and Cognito.4 will flag this).** It needs the
  Plus plan, which at a few users is pennies, so cost is not the reason. AWS's own
  guide says to run it in audit mode for two weeks before enforcing, its default
  enforced response blocks sign-in at every risk level, and its adaptive part
  requires MFA to be optional. A once-a-year sign-in from a new laptop is exactly
  what it scores as risky. Required MFA covers the same threat here.
- **Essentials plan, Cognito's built-in email.** Managed login is not available on
  Lite. Essentials is free up to 10,000 monthly users. AWS says built-in email's
  daily cap is too low for a typical production app; invites and resets for a
  handful of staff are nowhere near it, and the alternative needs an SES identity.
- **Callback and logout URLs: the site URL with and without a trailing slash.**
  Cognito matches `redirect_uri` exactly, and that mismatch is the classic
  first-deploy error.
- **Refresh tokens last 1 day, not the default 30.** Tokens sit in the browser.
  Signing in again once a day costs the owner nothing.
- **Default `RETAIN` on the pool.** Consistent with the buckets.

## 8. API Gateway REST API

- **Second departure from the diagram: step 4's poll goes through the API, not
  straight to S3.** The diagram has the browser read the marker from the data
  bucket. The browser holds a Cognito token, not AWS credentials, so a literal
  build needs a publicly readable object in the data bucket. Instead `GET /refresh`
  reads the marker through an S3 integration, behind the same sign-in as everything
  else. The API still has the diagram's two endpoints; Refresh has two methods. The
  diagram's step 4 arrow should be redrawn through the Refresh endpoint.
- **That `GET` is a direct S3 integration with its own role, not a third Lambda.**
  The role reads the marker key and nothing else. A missing marker comes back as
  `200 {"finished_at": null}` rather than 404, so a normal poll does not fill the
  browser console with errors. Any other S3 error maps to 502; unmapped, API
  Gateway would hand it back as a 200. Not exercised: needs a deploy.
- **Cognito authorizer set once as the default for every method.** A method added
  later is protected unless someone opts it out. Only the CORS `OPTIONS` methods
  are open, which browsers require.
- **The page sends the ID token.** AWS documents both: ID token for "who is this",
  access token with custom scopes for "what may they do". Everyone who can sign in
  may do everything here, so scopes and a resource server would be machinery with
  no job.
- **`POST /refresh` is a non-proxy Lambda integration with
  `X-Amz-Invocation-Type: 'Event'`.** That header is how AWS documents an
  asynchronous invoke, and it only exists on non-proxy integrations. It answers
  `202 {"started": true}`. The function gets `{}`; other content types get 415.
- **`GET /generate` is a plain Lambda proxy integration, no request validator.**
  The function already validates `ref_date` and explains itself. A validator would
  be a second copy of the rule with a vaguer message.
- **Regional endpoint.** CDK's default, edge-optimized, puts a hidden CloudFront in
  front for worldwide callers. The callers are on one campus.
- **CORS limited to the site's origin, and added to API Gateway's own 4xx and 5xx
  responses.** Without the second part an expired token reaches the page as an
  unreadable CORS failure instead of a 401 it can act on.
- **Throttled to 5 requests a second, burst 10.** One owner, polling every few
  seconds. Every Refresh is a full pull against Planon, so a runaway page should
  hit a wall here first.
- **Access log to a 13 month log group, with the signed-in user's email on every
  line.** This is the record of who pulled Planon data and when.
- **Execution logging at `ERROR`, payloads never logged; X-Ray on.** The two
  non-Lambda integrations have no logs of their own, so this is the only place
  their failures show up. Clears Security Hub APIGateway.1 and APIGateway.3.
  API Gateway makes that log group itself, with no retention set.
- **The stack does not set the account-wide API Gateway logging role.** Logging
  needs it (AWS docs, "Permissions for CloudWatch logging"), but it is one setting
  per account and region, and CDK's recommended `disableCloudWatchRole` flag exists
  because a stack that owns it can break other APIs' logging when it is deleted.
  **If the account has none, the first deploy fails at the stage.** The check and
  the fix are in `cdk/README.md`.
- **Stage is `prod`; no custom domain, API keys or usage plan.** None are in the
  diagram. Cognito is the access control.
- **`cdk/README.md` documents the three calls.** The page is not written yet, and
  the response shapes are choices made here.

## Standards applied, and findings accepted

Kyle's rule for this stack is to follow AWS standards. After building, every
resource was checked against AWS's own published controls (the Security Hub control
reference, read 2026-09-21). The line drawn: **a standard that is a setting on a
resource the diagram already has was adopted; one that needs a new component, a
monthly charge, or a campus decision is listed here instead.**

Adopted because of that review: bucket versioning and lifecycle (S3.10, S3.13,
S3.14), required MFA (Cognito.5), pool deletion protection (Cognito.6), X-Ray
(Lambda.7, APIGateway.3), execution logging (APIGateway.1), packaged `boto3`.
Already passing: S3.2, S3.3, S3.5, S3.8, CloudFront.1, CloudFront.3, CloudFront.13,
Cognito.3, Lambda.1, Lambda.2.

Accepted. Security Hub will flag these, and each is a decision for campus, not a bug:

| Control | What it wants | Why not here |
| --- | --- | --- |
| CloudFront.6, APIGateway.4 | WAF web ACLs | New components, roughly $5 a month each plus rules. The API already needs a sign-in on every call and is throttled. The first thing to add if campus security asks. |
| CloudFront.7, CloudFront.8 | Custom certificate, SNI | Needs a campus domain name. Until then CloudFront pins the default certificate to its `TLSv1` policy (AWS docs), so the minimum TLS version cannot be raised. With a domain, use `TLSv1.2_2021` or newer. |
| CloudFront.5, S3.9 | CloudFront and S3 access logs | Needs a log bucket. CloudFront.5 only accepts legacy logging, which needs ACLs on that bucket, which S3.12 then flags. The API access log already records who did what. |
| CloudFront.4, S3.7 | Origin failover, cross-region replication | A second region for a once-a-year report. |
| CloudFront.17 | Signed URLs or cookies | The page is a public shell with no data in it. All data is behind the API. |
| S3.17 | KMS keys on buckets | See section 3. |
| S3.11, S3.15 | Event notifications, Object Lock | Nothing would consume the events. Object Lock stops a pull from replacing the tables. |
| SecretsManager.1, .4 | Rotation | See section 4. |
| **SecretsManager.3** | Secret used in the last 90 days | **It is only read when someone clicks Refresh, so it will be flagged as unused for most of the year. Do not delete it.** |
| Cognito.1, Cognito.4 | Threat protection | See section 7. |
| Lambda.3 | Functions in a VPC | See the VPC entry in section 5. |
| APIGateway.2 | Client certificate for the backend | Both backends are AWS services called with IAM credentials; a client certificate is never presented. |
