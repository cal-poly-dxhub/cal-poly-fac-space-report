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
- **`cdk.json` is `cdk init` output (CLI 2.1129.0) plus the two flags that CLI's
  template lags behind the library on.** Result matches the library's own
  `recommended-feature-flags.json` exactly. New apps should start on current
  defaults; a trimmed list silently means older behavior.
- **`@aws-cdk/core:validateAgainstDefaultRules` is `true`.** One of those two
  flags. It turns CloudFormation validation findings from warnings into synth
  failures, which makes "clean synth" a stronger claim.
- **No `env` on the stack (account- and region-agnostic).** The campus account and
  region are not in the diagram and the target is synth, not deploy. `app.py`
  marks the one line to change.
- **One tag, `Project=fac-space-report`, on everything.** So campus staff can find
  and cost-allocate these resources. Not in the diagram; cheap and conventional.
- **`cdk/.gitignore` rather than editing the root one.** Keeps the CDK app
  self-contained. Ignores `cdk.out/` and the venv.
