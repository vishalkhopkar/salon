import os

from aws_cdk import (
    BundlingOptions,
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_events as events,
    aws_events_targets as targets,
    aws_lambda as _lambda,
    aws_s3 as s3,
)
from constructs import Construct

from bundling import PipLocalBundling

INFRA_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_DIR = os.path.normpath(os.path.join(INFRA_DIR, ".."))
OBJECT_KEY = "salons.json"
PYTHON_VERSION = "3.12"


class SalonStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Dev/learning project: DESTROY + auto_delete_objects so `cdk destroy` tears down
        # cleanly. Switch to RETAIN before this ever holds data you can't afford to lose.
        bucket = s3.Bucket(
            self, "SalonsBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        generator_fn = _lambda.Function(
            self, "GeneratorFunction",
            runtime=_lambda.Runtime.PYTHON_3_12,
            architecture=_lambda.Architecture.X86_64,
            handler="find_salon.lambda_handler",
            code=_lambda.Code.from_asset(
                SCRIPT_DIR,
                exclude=["infra", "salons.json", "salons.json.bak_pre_expiry", "__pycache__", "*.pyc"],
                bundling=BundlingOptions(
                    image=_lambda.Runtime.PYTHON_3_12.bundling_image,
                    local=PipLocalBundling(SCRIPT_DIR, ["find_salon.py"], PYTHON_VERSION),
                ),
            ),
            timeout=Duration.minutes(15),
            memory_size=512,
            environment={
                "SALONS_BUCKET_NAME": bucket.bucket_name,
                "SALONS_OBJECT_KEY": OBJECT_KEY,
            },
        )
        bucket.grant_read(generator_fn)
        bucket.grant_put(generator_fn)

        # This distribution only ever serves one object, so every request path - "/",
        # "/salons", anything - is rewritten at the edge to fetch that fixed S3 key.
        rewrite_to_object_fn = cloudfront.Function(
            self, "RewriteToObjectKey",
            code=cloudfront.FunctionCode.from_inline(
                f"function handler(event) {{\n"
                f"    var request = event.request;\n"
                f"    request.uri = '/{OBJECT_KEY}';\n"
                f"    return request;\n"
                f"}}"
            ),
            runtime=cloudfront.FunctionRuntime.JS_2_0,
        )

        # CACHING_OPTIMIZED doesn't vary its cache key by the Origin header, so different
        # edge locations independently cache whichever response (with or without the CORS
        # header) happened to hit them first - including Origin here fixes that.
        cors_aware_cache_policy = cloudfront.CachePolicy(
            self, "CorsAwareCachePolicy",
            header_behavior=cloudfront.CacheHeaderBehavior.allow_list("Origin"),
            default_ttl=Duration.hours(24),
            min_ttl=Duration.seconds(0),
            max_ttl=Duration.days(365),
        )

        distribution = cloudfront.Distribution(
            self, "SalonsDistribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                cache_policy=cors_aware_cache_policy,
                response_headers_policy=cloudfront.ResponseHeadersPolicy.CORS_ALLOW_ALL_ORIGINS,
                function_associations=[
                    cloudfront.FunctionAssociation(
                        event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                        function=rewrite_to_object_fn,
                    ),
                ],
            ),
        )

        # Runs daily at 00:00 UTC - adjust the hour if you want local-midnight in a
        # specific timezone instead.
        rule = events.Rule(
            self, "NightlyGeneratorTrigger",
            schedule=events.Schedule.cron(minute="0", hour="0"),
        )
        rule.add_target(targets.LambdaFunction(generator_fn))

        CfnOutput(self, "SalonsUrl", value=f"https://{distribution.distribution_domain_name}/salons")
        CfnOutput(self, "BucketName", value=bucket.bucket_name)
        CfnOutput(self, "GeneratorFunctionName", value=generator_fn.function_name)
