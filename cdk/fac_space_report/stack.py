from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_s3 as s3
from constructs import Construct


class FacSpaceReportStack(Stack):
    """Everything inside the "AWS account" box of docs/aws-deployment.drawio."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # "S3 static site". The bucket is private; CloudFront is the only way in.
        site_bucket = s3.Bucket(
            self,
            "SiteBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
        )
        site = cloudfront.Distribution(
            self,
            "SiteDistribution",
            comment="fac-space-report static site",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                response_headers_policy=cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
            ),
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
        )
        self.site_url = f"https://{site.distribution_domain_name}"

        CfnOutput(self, "SiteUrl", value=self.site_url)
        CfnOutput(self, "SiteBucketName", value=site_bucket.bucket_name)
