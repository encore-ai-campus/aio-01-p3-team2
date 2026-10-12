"""Deterministic CloudFormation sources. No credentials or account-specific values."""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ROLES = ("plan-web", "game-web", "main-api", "fault-api", "main-mcp", "fault-mcp")
PREFIX = "/multi-agent-autohealing/ikonly-demo/"
PARAMETERS = {
    "plan-web": ["plan/basic-auth-hash", "proof/plan-main", "proof/plan-fault"],
    "game-web": ["game/basic-auth-hash", "proof/game-main"],
    "main-api": [
        "shared/openai-api-key",
        "main/runtime-database-url",
        "main/llm-record-key",
        "proof/plan-main",
        "proof/game-main",
    ],
    "fault-api": ["fault/runtime-database-url", "fault/llm-record-key", "proof/plan-fault"],
    "main-mcp": ["main/mcp-database-url"],
    "fault-mcp": ["fault/mcp-database-url"],
}


def ref(name):
    return {"Ref": name}


def sub(value):
    return {"Fn::Sub": value}


def att(name, value="Arn"):
    return {"Fn::GetAtt": [name, value]}


def logical(role):
    return "".join(part.title() for part in role.split("-"))


def tags(role=None):
    result = [
        {"Key": "Environment", "Value": "ikonly-demo"},
        {"Key": "ManagedBy", "Value": "ikonly-iac"},
        {"Key": "OwnerStack", "Value": ref("AWS::StackName")},
    ]
    if role:
        result.append({"Key": "ServiceRole", "Value": role})
    return result


def template(description, parameters=None):
    return {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": description,
        "Parameters": parameters or {},
        "Resources": {},
        "Outputs": {},
    }


def parameter(default=None, **kwargs):
    result = {"Type": "String", **kwargs}
    if default is not None:
        result["Default"] = default
    return result


def resource(kind, **properties):
    return {"Type": "AWS::" + kind, "Properties": properties}


def statement(actions, resources, condition=None):
    result = {"Effect": "Allow", "Action": actions, "Resource": resources}
    if condition:
        result["Condition"] = condition
    return result


def policy(statements):
    return {"Version": "2012-10-17", "Statement": statements}


def network():
    t = template(
        "Independent ikonly network; no SSH; one NAT, two DB AZs",
        {
            "OperatorCidr": parameter(AllowedPattern=r"[0-9.]+/(3[0-2]|[12]?[0-9])"),
        },
    )
    r = t["Resources"]
    r["Vpc"] = resource(
        "EC2::VPC",
        CidrBlock="10.72.0.0/16",
        EnableDnsSupport=True,
        EnableDnsHostnames=True,
        Tags=tags(),
    )
    r["Igw"] = resource("EC2::InternetGateway", Tags=tags())
    r["IgwAttachment"] = resource(
        "EC2::VPCGatewayAttachment", VpcId=ref("Vpc"), InternetGatewayId=ref("Igw")
    )
    for name, cidr, az in [
        ("Public", "10.72.0.0/24", 0),
        ("App", "10.72.10.0/24", 0),
        ("DbA", "10.72.20.0/24", 0),
        ("DbB", "10.72.21.0/24", 1),
    ]:
        r[name] = resource(
            "EC2::Subnet",
            VpcId=ref("Vpc"),
            CidrBlock=cidr,
            AvailabilityZone={"Fn::Select": [az, {"Fn::GetAZs": ""}]},
            MapPublicIpOnLaunch=False,
            Tags=tags(),
        )
    r["NatEip"] = resource("EC2::EIP", Domain="vpc", Tags=tags())
    r["Nat"] = resource(
        "EC2::NatGateway",
        SubnetId=ref("Public"),
        AllocationId=att("NatEip", "AllocationId"),
        Tags=tags(),
    )
    r["Nat"]["DependsOn"] = "IgwAttachment"
    for name, gateway, subnet in [("PublicRoute", "Igw", "Public"), ("AppRoute", "Nat", "App")]:
        r[name + "Table"] = resource("EC2::RouteTable", VpcId=ref("Vpc"), Tags=tags())
        r[name] = resource(
            "EC2::Route",
            RouteTableId=ref(name + "Table"),
            DestinationCidrBlock="0.0.0.0/0",
            **{"GatewayId" if gateway == "Igw" else "NatGatewayId": ref(gateway)},
        )
        if gateway == "Igw":
            r[name]["DependsOn"] = "IgwAttachment"
        r[name + "Association"] = resource(
            "EC2::SubnetRouteTableAssociation",
            RouteTableId=ref(name + "Table"),
            SubnetId=ref(subnet),
        )
    # DB subnets deliberately have only the VPC-local route.
    for role in (*ROLES, "database"):
        name = logical(role) + "Sg"
        r[name] = resource(
            "EC2::SecurityGroup",
            GroupDescription="ikonly " + role,
            VpcId=ref("Vpc"),
            Tags=tags(role),
            SecurityGroupEgress=[
                {"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443, "CidrIp": "0.0.0.0/0"}
            ]
            if role != "database"
            else [{"IpProtocol": "tcp", "FromPort": 1, "ToPort": 1, "CidrIp": "127.0.0.1/32"}],
        )
        t["Outputs"][name] = {"Value": ref(name)}
        if role != "database":
            r[name + "HttpEgress"] = resource(
                "EC2::SecurityGroupEgress",
                GroupId=ref(name),
                IpProtocol="tcp",
                FromPort=80,
                ToPort=80,
                CidrIp="0.0.0.0/0",
            )
    for role in ROLES[:2]:
        for port in (80, 443):
            r[logical(role) + str(port)] = resource(
                "EC2::SecurityGroupIngress",
                GroupId=ref(logical(role) + "Sg"),
                IpProtocol="tcp",
                FromPort=port,
                ToPort=port,
                CidrIp=ref("OperatorCidr"),
            )
    edges = [
        ("plan-web", "main-api", 8000),
        ("plan-web", "fault-api", 8000),
        ("game-web", "main-api", 8000),
        ("main-api", "main-mcp", 8010),
        ("fault-api", "fault-mcp", 8010),
    ]
    edges += [(role, "database", 5432) for role in ROLES[2:]]
    for source, target, port in edges:
        key = logical(source) + "To" + logical(target)
        r[key + "Ingress"] = resource(
            "EC2::SecurityGroupIngress",
            GroupId=ref(logical(target) + "Sg"),
            SourceSecurityGroupId=ref(logical(source) + "Sg"),
            IpProtocol="tcp",
            FromPort=port,
            ToPort=port,
        )
        r[key + "Egress"] = resource(
            "EC2::SecurityGroupEgress",
            GroupId=ref(logical(source) + "Sg"),
            DestinationSecurityGroupId=ref(logical(target) + "Sg"),
            IpProtocol="tcp",
            FromPort=port,
            ToPort=port,
        )
    for name in ("Vpc", "Public", "App", "DbA", "DbB", "Nat", "NatEip"):
        t["Outputs"][name] = {"Value": ref(name)}
    return t


def data():
    t = template(
        "Private encrypted PostgreSQL; secure parameter reference, deletion protection",
        {
            **{name: parameter() for name in ("DbA", "DbB", "DatabaseSg", "EngineVersion")},
            "MasterPasswordVersion": {"Type": "Number", "MinValue": 1},
        },
    )
    r = t["Resources"]
    r["SubnetGroup"] = resource(
        "RDS::DBSubnetGroup",
        DBSubnetGroupDescription="ikonly private DB",
        SubnetIds=[ref("DbA"), ref("DbB")],
        Tags=tags(),
    )
    r["Parameters"] = resource(
        "RDS::DBParameterGroup",
        Description="Require TLS",
        Family="postgres16",
        Parameters={"rds.force_ssl": "1"},
        Tags=tags(),
    )
    r["Database"] = resource(
        "RDS::DBInstance",
        DBInstanceIdentifier="ikonly-demo-postgres",
        Engine="postgres",
        EngineVersion=ref("EngineVersion"),
        DBInstanceClass="db.t4g.small",
        AllocatedStorage="20",
        MaxAllocatedStorage=20,
        StorageType="gp3",
        StorageEncrypted=True,
        MultiAZ=False,
        PubliclyAccessible=False,
        DBSubnetGroupName=ref("SubnetGroup"),
        VPCSecurityGroups=[ref("DatabaseSg")],
        DBParameterGroupName=ref("Parameters"),
        MasterUsername="eventquest_bootstrap",
        ManageMasterUserPassword=False,
        MasterUserPassword=sub(
            "{{resolve:ssm-secure:"
            + PREFIX
            + "operations/master-password:${MasterPasswordVersion}}}"
        ),
        BackupRetentionPeriod=7,
        DeleteAutomatedBackups=True,
        CopyTagsToSnapshot=True,
        DeletionProtection=True,
        EnableCloudwatchLogsExports=["postgresql", "upgrade"],
        AutoMinorVersionUpgrade=True,
        Tags=tags("database"),
    )
    # Reset is an explicitly confirmed purge. Avoid creating a final snapshot
    # just to delete it immediately; replacement and migration remain protected.
    r["Database"].update(DeletionPolicy="Delete", UpdateReplacePolicy="Snapshot")
    for logical_name, suffix in (("PostgresqlLogs", "postgresql"), ("UpgradeLogs", "upgrade")):
        r[logical_name] = resource(
            "Logs::LogGroup",
            LogGroupName="/aws/rds/instance/ikonly-demo-postgres/" + suffix,
            RetentionInDays=14,
            Tags=tags("database"),
        )
        r[logical_name].update(DeletionPolicy="Retain", UpdateReplacePolicy="Retain")
    r["Database"]["DependsOn"] = ["PostgresqlLogs", "UpgradeLogs"]
    t["Outputs"]["Endpoint"] = {"Value": att("Database", "Endpoint.Address")}
    t["Outputs"]["DatabaseId"] = {"Value": ref("Database")}
    return t


def access():
    t = template(
        "Runtime profiles with explicit parameter scope; GitHub roles initially disabled",
        {
            "EnableGithubRoles": parameter("false", AllowedValues=["true", "false"]),
            "GithubOidcProviderArn": parameter(""),
        },
    )
    t["Conditions"] = {"GithubEnabled": {"Fn::Equals": [ref("EnableGithubRoles"), "true"]}}
    r = t["Resources"]
    repos = []
    for image in ("application", "web"):
        name = image.title() + "Repository"
        r[name] = resource(
            "ECR::Repository",
            RepositoryName="ikonly-demo/" + image,
            ImageTagMutability="IMMUTABLE",
            ImageScanningConfiguration={"ScanOnPush": True},
            EncryptionConfiguration={"EncryptionType": "AES256"},
            Tags=tags(),
        )
        r[name].update(DeletionPolicy="Retain", UpdateReplacePolicy="Retain")
        repos.append(att(name))
        t["Outputs"][name] = {"Value": att(name)}
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "ec2.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    for role in (*ROLES, "bootstrap", "migration"):
        name = logical(role)
        paths = PARAMETERS.get(role, ["operations/main-owner-url", "operations/fault-owner-url"])
        if role == "bootstrap":
            paths = [
                "operations/master-password",
                "operations/master-url",
                "operations/role-passwords",
                "operations/main-owner-url",
                "operations/fault-owner-url",
            ]
        scopes = [
            sub(
                "arn:${AWS::Partition}:ssm:${AWS::Region}:${AWS::AccountId}:parameter"
                + PREFIX
                + path
            )
            for path in paths
        ]
        statements = [
            statement(["ssm:GetParameter", "ssm:GetParameters"], scopes),
            statement(["ecr:GetAuthorizationToken"], "*"),
            statement(
                [
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchCheckLayerAvailability",
                ],
                repos
                if role in ("bootstrap", "migration")
                else [repos[1 if role.endswith("web") else 0]],
            ),
            statement(
                ["logs:CreateLogStream", "logs:PutLogEvents"],
                sub(
                    "arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:/ikonly-demo/"
                    + role
                    + ":log-stream:*"
                ),
            ),
        ]
        # AmazonSSMManagedInstanceCore grants GetParameter(s) on '*'. Use only
        # agent/channel permissions here so the secret scopes above remain effective.
        statements += [
            statement(
                [
                    "ssm:UpdateInstanceInformation",
                    "ssm:DescribeAssociation",
                    "ssm:ListInstanceAssociations",
                    "ssm:ListAssociations",
                    "ssm:GetDocument",
                    "ssm:DescribeDocument",
                    "ssm:UpdateAssociationStatus",
                    "ssm:UpdateInstanceAssociationStatus",
                ],
                "*",
            ),
            statement(
                [
                    "ssmmessages:CreateControlChannel",
                    "ssmmessages:CreateDataChannel",
                    "ssmmessages:OpenControlChannel",
                    "ssmmessages:OpenDataChannel",
                ],
                "*",
            ),
            statement(
                [
                    "ec2messages:AcknowledgeMessage",
                    "ec2messages:DeleteMessage",
                    "ec2messages:FailMessage",
                    "ec2messages:GetEndpoint",
                    "ec2messages:GetMessages",
                    "ec2messages:SendReply",
                ],
                "*",
            ),
        ]
        r[name + "Role"] = resource(
            "IAM::Role",
            RoleName="ikonly-demo-" + role,
            AssumeRolePolicyDocument=trust,
            Policies=[{"PolicyName": "ScopedRuntime", "PolicyDocument": policy(statements)}],
            Tags=tags(role),
        )
        r[name + "Profile"] = resource("IAM::InstanceProfile", Roles=[ref(name + "Role")])
        t["Outputs"][name + "Profile"] = {"Value": ref(name + "Profile")}
    r["DeployDocument"] = resource(
        "SSM::Document",
        Name="IkonlyDeploy",
        DocumentType="Command",
        UpdateMethod="NewVersion",
        Content=deploy_document(),
        Tags=tags(),
    )
    # An existing, account-level OIDC provider is supplied; never adopt/delete somebody else's provider.
    for label, environment in [
        ("Deploy", "ikonly-demo"),
        ("Infrastructure", "ikonly-demo"),
    ]:
        oidc_trust = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Federated": ref("GithubOidcProviderArn")},
                    "Action": "sts:AssumeRoleWithWebIdentity",
                    "Condition": {
                        "StringEquals": {
                            "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                            "token.actions.githubusercontent.com:sub": "repo:ik-e/multi-agent-autohealing:environment:"
                            + environment,
                        }
                    },
                }
            ],
        }
        statements = [statement(["sts:GetCallerIdentity"], "*")]
        if label == "Deploy":
            statements += [
                statement(["ecr:GetAuthorizationToken"], "*"),
                statement(
                    [
                        "ecr:BatchCheckLayerAvailability",
                        "ecr:InitiateLayerUpload",
                        "ecr:UploadLayerPart",
                        "ecr:CompleteLayerUpload",
                        "ecr:PutImage",
                        "ecr:DescribeImages",
                        "ecr:BatchGetImage",
                    ],
                    repos,
                ),
                statement(
                    ["ssm:SendCommand"],
                    sub(
                        "arn:${AWS::Partition}:ssm:${AWS::Region}:${AWS::AccountId}:document/IkonlyDeploy"
                    ),
                ),
                statement(
                    ["ssm:SendCommand"],
                    sub("arn:${AWS::Partition}:ec2:${AWS::Region}:${AWS::AccountId}:instance/*"),
                    {
                        "StringEquals": {
                            "ssm:resourceTag/Environment": "ikonly-demo",
                            "ssm:resourceTag/ManagedBy": "ikonly-iac",
                        }
                    },
                ),
                statement(["ssm:GetCommandInvocation", "ssm:ListCommandInvocations"], "*"),
            ]
        else:
            # Planning only. Broad CloudFormation execution/PassRole is deliberately not granted.
            statements += [
                statement(
                    [
                        "cloudformation:CreateChangeSet",
                        "cloudformation:DescribeChangeSet",
                        "cloudformation:DescribeStacks",
                        "cloudformation:GetTemplate",
                        "cloudformation:ListStackResources",
                    ],
                    [
                        sub(
                            "arn:${AWS::Partition}:cloudformation:${AWS::Region}:${AWS::AccountId}:stack/ikonly-demo-*/*"
                        ),
                        sub(
                            "arn:${AWS::Partition}:cloudformation:${AWS::Region}:${AWS::AccountId}:changeSet/ikonly-*/*"
                        ),
                    ],
                ),
                statement(
                    [
                        "cloudformation:ValidateTemplate",
                        "ec2:DescribeImages",
                        "rds:DescribeDBEngineVersions",
                    ],
                    "*",
                ),
            ]
        r[label + "GithubRole"] = resource(
            "IAM::Role",
            RoleName="ikonly-demo-github-" + label.lower(),
            AssumeRolePolicyDocument=oidc_trust,
            MaxSessionDuration=3600,
            Policies=[{"PolicyName": "Scoped" + label, "PolicyDocument": policy(statements)}],
            Tags=tags(),
        )
        r[label + "GithubRole"]["Condition"] = "GithubEnabled"
        t["Outputs"][label + "GithubRole"] = {
            "Value": att(label + "GithubRole"),
            "Condition": "GithubEnabled",
        }
    return t


def application():
    names = ["Public", "App", "AmiId"] + [
        logical(role) + suffix for role in ROLES for suffix in ("Sg", "Profile")
    ]
    t = template(
        "Six separate Docker/SSM hosts; bootstraps only, no application release",
        {name: parameter() for name in names},
    )
    t["Parameters"]["AmiId"] = {"Type": "AWS::EC2::Image::Id"}
    r = t["Resources"]
    bootstrap = (ROOT / "scripts/bootstrap-host.sh").read_text(encoding="utf-8")
    for role in ROLES:
        name = logical(role)
        r[name] = resource(
            "EC2::Instance",
            ImageId=ref("AmiId"),
            InstanceType="t3.medium" if role.endswith("api") else "t3.small",
            CreditSpecification={"CPUCredits": "standard"},
            IamInstanceProfile=ref(name + "Profile"),
            MetadataOptions={
                "HttpTokens": "required",
                "HttpPutResponseHopLimit": 1,
                "HttpEndpoint": "enabled",
                "HttpProtocolIpv6": "disabled",
            },
            NetworkInterfaces=[
                {
                    "DeviceIndex": "0",
                    "SubnetId": ref("Public" if role.endswith("web") else "App"),
                    "GroupSet": [ref(name + "Sg")],
                    "AssociatePublicIpAddress": role.endswith("web"),
                }
            ],
            BlockDeviceMappings=[
                {
                    "DeviceName": "/dev/sda1",
                    "Ebs": {
                        "Encrypted": True,
                        "VolumeType": "gp3",
                        "VolumeSize": 20,
                        "DeleteOnTermination": True,
                    },
                }
            ],
            UserData={"Fn::Base64": bootstrap.replace("__SERVICE_ROLE__", role)},
            Tags=tags(role),
        )
        t["Outputs"][name] = {"Value": ref(name)}
        t["Outputs"][name + "PrivateIp"] = {"Value": att(name, "PrivateIp")}
        if role.endswith("web"):
            r[name + "Eip"] = resource("EC2::EIP", Domain="vpc", Tags=tags(role))
            r[name + "Association"] = resource(
                "EC2::EIPAssociation",
                InstanceId=ref(name),
                AllocationId=att(name + "Eip", "AllocationId"),
            )
            t["Outputs"][name + "PublicIp"] = {"Value": ref(name + "Eip")}
    return t


def observability():
    t = template(
        "Retained evidence and logs; SNS email requires recipient confirmation",
        {
            "NotificationEmail": parameter(AllowedPattern=r"[^\s@]+@[^\s@]+\.[^\s@]+"),
            **{logical(role) + "Instance": parameter("") for role in ROLES},
        },
    )
    r = t["Resources"]
    r["Topic"] = resource(
        "SNS::Topic",
        Subscription=[{"Protocol": "email", "Endpoint": ref("NotificationEmail")}],
        Tags=tags(),
    )
    t["Conditions"] = {
        logical(role) + "Exists": {
            "Fn::Not": [{"Fn::Equals": [ref(logical(role) + "Instance"), ""]}]
        }
        for role in ROLES
    }
    for role in (*ROLES, "bootstrap", "migration"):
        r[logical(role) + "Logs"] = resource(
            "Logs::LogGroup",
            LogGroupName="/ikonly-demo/" + role,
            RetentionInDays=14,
            Tags=tags(role),
        )
        r[logical(role) + "Logs"].update(DeletionPolicy="Retain", UpdateReplacePolicy="Retain")
        if role in ROLES:
            for metric, threshold, comparison in [
                ("CPUUtilization", 80, "GreaterThanThreshold"),
                ("CPUCreditBalance", 10, "LessThanThreshold"),
                ("StatusCheckFailed", 0, "GreaterThanThreshold"),
            ]:
                key = logical(role) + metric
                r[key] = resource(
                    "CloudWatch::Alarm",
                    Namespace="AWS/EC2",
                    MetricName=metric,
                    Dimensions=[{"Name": "InstanceId", "Value": ref(logical(role) + "Instance")}],
                    Statistic="Average",
                    Period=300,
                    EvaluationPeriods=2,
                    Threshold=threshold,
                    ComparisonOperator=comparison,
                    TreatMissingData="missing",
                    AlarmActions=[ref("Topic")],
                    Tags=tags(role),
                )
                r[key]["Condition"] = logical(role) + "Exists"
    r["DatabaseDisk"] = resource(
        "CloudWatch::Alarm",
        Namespace="AWS/RDS",
        MetricName="FreeStorageSpace",
        Dimensions=[{"Name": "DBInstanceIdentifier", "Value": "ikonly-demo-postgres"}],
        Statistic="Minimum",
        Period=300,
        EvaluationPeriods=2,
        Threshold=3221225472,
        ComparisonOperator="LessThanThreshold",
        TreatMissingData="missing",
        AlarmActions=[ref("Topic")],
        Tags=tags("database"),
    )
    r["Evidence"] = resource(
        "S3::Bucket",
        BucketEncryption={
            "ServerSideEncryptionConfiguration": [
                {"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
            ]
        },
        PublicAccessBlockConfiguration={
            key: True
            for key in (
                "BlockPublicAcls",
                "IgnorePublicAcls",
                "BlockPublicPolicy",
                "RestrictPublicBuckets",
            )
        },
        OwnershipControls={"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]},
        VersioningConfiguration={"Status": "Enabled"},
        LifecycleConfiguration={
            "Rules": [
                {
                    "Id": "Evidence30Days",
                    "Status": "Enabled",
                    "Prefix": "evidence/",
                    "ExpirationInDays": 30,
                    "NoncurrentVersionExpiration": {"NoncurrentDays": 30},
                },
                {
                    "Id": "Backups7Days",
                    "Status": "Enabled",
                    "Prefix": "backups/",
                    "ExpirationInDays": 7,
                    "NoncurrentVersionExpiration": {"NoncurrentDays": 7},
                },
            ]
        },
        Tags=tags(),
    )
    r["Evidence"].update(DeletionPolicy="Retain", UpdateReplacePolicy="Retain")
    r["EvidencePolicy"] = resource(
        "S3::BucketPolicy",
        Bucket=ref("Evidence"),
        PolicyDocument={
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Deny",
                    "Principal": "*",
                    "Action": "s3:*",
                    "Resource": [att("Evidence"), sub("${Evidence.Arn}/*")],
                    "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                }
            ],
        },
    )
    t["Outputs"]["EvidenceBucket"] = {"Value": ref("Evidence")}
    t["Outputs"]["AlarmTopic"] = {"Value": ref("Topic")}
    return t


def deploy_document():
    return {
        "schemaVersion": "2.2",
        "description": "Run only the installed reviewed release helper",
        "parameters": {
            "ReleaseId": {
                "type": "String",
                "allowedPattern": "^[a-f0-9]{40}$",
                "interpolationType": "ENV_VAR",
            },
            "ManifestSha256": {
                "type": "String",
                "allowedPattern": "^[a-f0-9]{64}$",
                "interpolationType": "ENV_VAR",
            },
        },
        "mainSteps": [
            {
                "action": "aws:runShellScript",
                "name": "VerifiedRelease",
                "inputs": {
                    "timeoutSeconds": "1800",
                    "runCommand": [
                        "set -eu",
                        "test -x /opt/eventquest/bin/deploy-release",
                        'exec /opt/eventquest/bin/deploy-release "$SSM_ReleaseId" "$SSM_ManifestSha256"',
                    ],
                },
            }
        ],
    }


def generated():
    return {
        **{
            f"stacks/{name}.yml": builder()
            for name, builder in (
                ("network", network),
                ("data", data),
                ("access", access),
                ("application", application),
                ("observability", observability),
            )
        },
        "ssm/IkonlyDeploy.yml": deploy_document(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    for relative, content in generated().items():
        path = ROOT / relative
        expected = json.dumps(content, indent=2, ensure_ascii=False) + "\n"
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != expected:
                raise SystemExit("Generated template differs: " + relative)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(expected, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
