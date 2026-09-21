#!/usr/bin/env python3
import aws_cdk as cdk

from fac_space_report.stack import FacSpaceReportStack

app = cdk.App()

# No env is set, so the template is account- and region-agnostic. Pass
# env=cdk.Environment(account=..., region=...) here once the campus account is known.
FacSpaceReportStack(
    app,
    "FacSpaceReportStack",
    description="fac-space-report: CSU facility report, per docs/aws-deployment.drawio",
)

cdk.Tags.of(app).add("Project", "fac-space-report")

app.synth()
