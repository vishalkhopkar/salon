import os

from aws_cdk import (
    BundlingOptions,
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_apigateway as apigateway,
    aws_events as events,
    aws_events_targets as targets,
    aws_lambda as _lambda,
    aws_s3 as s3,
)
from constructs import Construct

from bundling import PipLocalBundling

INFRA_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_DIR = os.path.normpath(os.path.join(INFRA_DIR, ".."))
READER_DIR = os.path.join(INFRA_DIR, "lambda_reader")
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

        reader_fn = _lambda.Function(
            self, "ReaderFunction",
            runtime=_lambda.Runtime.PYTHON_3_12,
            architecture=_lambda.Architecture.X86_64,
            handler="reader.handler",
            code=_lambda.Code.from_asset(READER_DIR),
            timeout=Duration.seconds(10),
            memory_size=128,
            environment={
                "SALONS_BUCKET_NAME": bucket.bucket_name,
                "SALONS_OBJECT_KEY": OBJECT_KEY,
            },
        )
        bucket.grant_read(reader_fn)

        api = apigateway.RestApi(
            self, "SalonsApi",
            rest_api_name="salons-api",
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=apigateway.Cors.ALL_ORIGINS,
                allow_methods=["GET"],
            ),
        )
        salons_resource = api.root.add_resource("salons")
        salons_resource.add_method("GET", apigateway.LambdaIntegration(reader_fn))

        # Runs daily at 00:00 UTC - adjust the hour if you want local-midnight in a
        # specific timezone instead.
        rule = events.Rule(
            self, "NightlyGeneratorTrigger",
            schedule=events.Schedule.cron(minute="0", hour="0"),
        )
        rule.add_target(targets.LambdaFunction(generator_fn))

        CfnOutput(self, "ApiUrl", value=f"{api.url}salons")
        CfnOutput(self, "BucketName", value=bucket.bucket_name)
        CfnOutput(self, "GeneratorFunctionName", value=generator_fn.function_name)
