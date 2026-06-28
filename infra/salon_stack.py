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
    aws_s3_deployment as s3_deploy,
)
from constructs import Construct

from bundling import PipLocalBundling

INFRA_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_DIR = os.path.normpath(os.path.join(INFRA_DIR, ".."))
WEB_DIR = os.path.join(SCRIPT_DIR, "web")
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
                exclude=["infra", "web", "salons.json", "salons.json.bak_pre_expiry", "__pycache__", "*.pyc"],
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

        # The distribution serves the static site at "/" and the data feed at "/salons" -
        # this rewrites each to the actual S3 key behind it. Any other path (e.g.
        # "/index.html" directly) passes through unchanged.
        rewrite_to_object_fn = cloudfront.Function(
            self, "RewriteToObjectKey",
            code=cloudfront.FunctionCode.from_inline(
                "function handler(event) {\n"
                "    var request = event.request;\n"
                "    if (request.uri === '/salons') {\n"
                f"        request.uri = '/{OBJECT_KEY}';\n"
                "    } else if (request.uri === '/') {\n"
                "        request.uri = '/index.html';\n"
                "    }\n"
                "    return request;\n"
                "}"
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
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
                cache_policy=cors_aware_cache_policy,
                response_headers_policy=cloudfront.ResponseHeadersPolicy.CORS_ALLOW_ALL_ORIGINS_WITH_PREFLIGHT,
                function_associations=[
                    cloudfront.FunctionAssociation(
                        event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                        function=rewrite_to_object_fn,
                    ),
                ],
            ),
        )

        # prune defaults to True, which deletes any object in the bucket not present in
        # this deployment's sources - since salons.json lives in the same bucket but is
        # written by the generator Lambda (not this deployment), that default would wipe
        # it out on every `cdk deploy`. prune=False scopes this to "add/update only".
        #
        # No content_type override here (unlike a single-file deployment) - it would force
        # every file in this source to the same MIME type, which is wrong for a mix of
        # .html/.css/.js. The underlying deploy step detects content-type per file extension.
        s3_deploy.BucketDeployment(
            self, "DeploySite",
            sources=[s3_deploy.Source.asset(WEB_DIR, exclude=["maps-api-key.example.js"])],
            destination_bucket=bucket,
            prune=False,
            distribution=distribution,
            distribution_paths=["/", "/index.html", "/style.css", "/app.js", "/maps-api-key.js"],
        )

        # Runs daily at 00:00 UTC - adjust the hour if you want local-midnight in a
        # specific timezone instead.
        rule = events.Rule(
            self, "NightlyGeneratorTrigger",
            schedule=events.Schedule.cron(minute="0", hour="0"),
        )
        rule.add_target(targets.LambdaFunction(generator_fn))

        CfnOutput(self, "SiteUrl", value=f"https://{distribution.distribution_domain_name}/")
        CfnOutput(self, "SalonsUrl", value=f"https://{distribution.distribution_domain_name}/salons")
        CfnOutput(self, "BucketName", value=bucket.bucket_name)
        CfnOutput(self, "GeneratorFunctionName", value=generator_fn.function_name)
