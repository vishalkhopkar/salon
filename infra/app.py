import aws_cdk as cdk

from salon_stack import SalonStack

app = cdk.App()
SalonStack(app, "SalonStack")
app.synth()
