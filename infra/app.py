import aws_cdk as cdk

from salon_stack import SalonStack

app = cdk.App()
SalonStack(app, "SalonStack", env=cdk.Environment(region="us-east-1"))
app.synth()
