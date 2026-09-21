from aws_cdk import Stack
from constructs import Construct


class FacSpaceReportStack(Stack):
    """Everything inside the "AWS account" box of docs/aws-deployment.drawio."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
