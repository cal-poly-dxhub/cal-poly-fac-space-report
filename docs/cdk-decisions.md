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
