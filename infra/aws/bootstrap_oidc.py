"""Locally bootstrap persistent, scoped OIDC roles; no application resources."""

import argparse
import json
from pathlib import Path

from infra.aws.host_release import run
from infra.aws.infrastructure import Aws, preflight, validate_config
from infra.aws.operation_state import write

REPO = "ik-e/multi-agent-autohealing"
CONTROL_TAGS = [
    {"Key": "Environment", "Value": "ikonly-control"},
    {"Key": "ManagedBy", "Value": "ikonly-control"},
    {"Key": "Repository", "Value": REPO},
]


def permission(actions, resources="*", condition=None):
    value = {"Effect": "Allow", "Action": actions, "Resource": resources}
    if condition:
        value["Condition"] = condition
    return value


def policies(account):
    prefix = "arn:aws:"
    ec2 = f"{prefix}ec2:ap-northeast-2:{account}:"
    ssm = f"{prefix}ssm:ap-northeast-2:{account}:"
    cfn = f"{prefix}cloudformation:ap-northeast-2:{account}:"
    iam = f"{prefix}iam::{account}:"
    roles = [iam + "role/ikonly-demo-*", iam + "instance-profile/ikonly-demo-access-*"]
    read = permission(
        [
            "sts:GetCallerIdentity",
            "ec2:Describe*",
            "rds:Describe*",
            "rds:ListTagsForResource",
            "cloudformation:Describe*",
            "cloudformation:GetTemplate",
            "cloudformation:List*",
            "ssm:DescribeParameters",
            "ssm:DescribeInstanceInformation",
            "ssm:GetCommandInvocation",
            "ssm:ListCommandInvocations",
            "ssm:ListTagsForResource",
            "logs:DescribeLogGroups",
            "logs:DescribeLogStreams",
            "logs:ListTagsLogGroup",
            "s3:ListAllMyBuckets",
            "ecr:DescribeRepositories",
            "ecr:GetAuthorizationToken",
            "iam:ListRoles",
            "iam:ListInstanceProfiles",
            "ssm:ListDocuments",
            "cloudwatch:DescribeAlarms",
            "sns:ListTopics",
        ]
    )
    controller = [
        read,
        permission(
            [
                "cloudformation:CreateChangeSet",
                "cloudformation:DeleteChangeSet",
                "cloudformation:ExecuteChangeSet",
                "cloudformation:DeleteStack",
            ],
            [cfn + "stack/ikonly-demo-*/*", cfn + "changeSet/ikonly-*/*"],
        ),
        permission(["cloudformation:ValidateTemplate"], "*"),
        permission(
            ["iam:PassRole"],
            iam + "role/ikonly-control-cloudformation",
            {"StringEquals": {"iam:PassedToService": "cloudformation.amazonaws.com"}},
        ),
        permission(
            ["iam:PassRole"],
            iam + "role/ikonly-demo-*",
            {"StringEquals": {"iam:PassedToService": "ec2.amazonaws.com"}},
        ),
        permission(
            ["ec2:ReplaceIamInstanceProfileAssociation"],
            ec2 + "instance/*",
            {
                "StringEquals": {
                    "ec2:ResourceTag/Environment": "ikonly-demo",
                    "ec2:ResourceTag/ManagedBy": "ikonly-iac",
                }
            },
        ),
        permission(
            ["ssm:SendCommand"],
            [
                ssm + "document/IkonlyDeploy",
                "arn:aws:ssm:ap-northeast-2::document/AWS-RunShellScript",
            ],
        ),
        permission(
            ["ssm:SendCommand"],
            ec2 + "instance/*",
            {
                "StringEquals": {
                    "ssm:resourceTag/Environment": "ikonly-demo",
                    "ssm:resourceTag/ManagedBy": "ikonly-iac",
                }
            },
        ),
        permission(
            ["ssm:GetParameters", "ssm:GetParameter"],
            [
                ssm + "parameter/multi-agent-autohealing/ikonly-demo/deployment/accepted",
                ssm + "parameter/multi-agent-autohealing/control/ikonly-demo/lock",
                "arn:aws:ssm:ap-northeast-2::parameter/aws/service/canonical/*",
            ],
        ),
        permission(
            ["ssm:PutParameter", "ssm:DeleteParameter", "ssm:AddTagsToResource"],
            [
                ssm + "parameter/multi-agent-autohealing/ikonly-demo/*",
                ssm + "parameter/multi-agent-autohealing/control/ikonly-demo/lock",
            ],
        ),
        permission(
            [
                "ecr:BatchCheckLayerAvailability",
                "ecr:InitiateLayerUpload",
                "ecr:UploadLayerPart",
                "ecr:CompleteLayerUpload",
                "ecr:PutImage",
                "ecr:DescribeImages",
                "ecr:BatchGetImage",
                "ecr:GetDownloadUrlForLayer",
                "ecr:ListTagsForResource",
                "ecr:DeleteRepository",
            ],
            f"arn:aws:ecr:ap-northeast-2:{account}:repository/ikonly-demo/*",
        ),
        permission(
            ["rds:ModifyDBInstance"],
            f"arn:aws:rds:ap-northeast-2:{account}:db:ikonly-demo-postgres",
        ),
        permission(
            ["rds:DeleteDBSnapshot"], f"arn:aws:rds:ap-northeast-2:{account}:snapshot:ikonly-demo-*"
        ),
        permission(
            ["rds:CreateDBSnapshot"],
            [
                f"arn:aws:rds:ap-northeast-2:{account}:db:ikonly-demo-postgres",
                f"arn:aws:rds:ap-northeast-2:{account}:snapshot:ikonly-demo-before-*",
            ],
        ),
        permission(
            ["rds:AddTagsToResource"],
            f"arn:aws:rds:ap-northeast-2:{account}:snapshot:ikonly-demo-before-*",
        ),
        permission(
            ["logs:FilterLogEvents", "logs:DeleteLogGroup", "logs:ListTagsForResource"],
            [
                f"arn:aws:logs:ap-northeast-2:{account}:log-group:/ikonly-demo/*",
                f"arn:aws:logs:ap-northeast-2:{account}:log-group:/aws/rds/instance/ikonly-demo-postgres/*",
            ],
        ),
        permission(
            [
                "s3:ListBucket",
                "s3:ListBucketVersions",
                "s3:GetBucketTagging",
                "s3:GetObject",
                "s3:GetObjectVersion",
                "s3:DeleteObject",
                "s3:DeleteObjectVersion",
                "s3:DeleteBucket",
            ],
            [
                "arn:aws:s3:::ikonly-demo-observability-evidence-*",
                "arn:aws:s3:::ikonly-demo-observability-evidence-*/*",
            ],
        ),
    ]
    cloudformation = [
        # These EC2 networking APIs do not all support a stack-name ARN. Scope
        # region and keep them in a service role that only owned CFN stacks use.
        permission(
            [
                "ec2:Describe*",
                "ec2:CreateVpc",
                "ec2:ModifyVpcAttribute",
                "ec2:DeleteVpc",
                "ec2:CreateSubnet",
                "ec2:ModifySubnetAttribute",
                "ec2:DeleteSubnet",
                "ec2:CreateInternetGateway",
                "ec2:AttachInternetGateway",
                "ec2:DetachInternetGateway",
                "ec2:DeleteInternetGateway",
                "ec2:AllocateAddress",
                "ec2:ReleaseAddress",
                "ec2:AssociateAddress",
                "ec2:DisassociateAddress",
                "ec2:CreateNatGateway",
                "ec2:DeleteNatGateway",
                "ec2:CreateRouteTable",
                "ec2:DeleteRouteTable",
                "ec2:CreateRoute",
                "ec2:DeleteRoute",
                "ec2:AssociateRouteTable",
                "ec2:DisassociateRouteTable",
                "ec2:CreateSecurityGroup",
                "ec2:DeleteSecurityGroup",
                "ec2:AuthorizeSecurityGroupIngress",
                "ec2:AuthorizeSecurityGroupEgress",
                "ec2:RevokeSecurityGroupIngress",
                "ec2:RevokeSecurityGroupEgress",
                "ec2:CreateVpcEndpoint",
                "ec2:DeleteVpcEndpoints",
                "ec2:ModifyVpcEndpoint",
                "ec2:RunInstances",
                "ec2:TerminateInstances",
                "ec2:ModifyInstanceAttribute",
                "ec2:CreateTags",
                "ec2:DeleteTags",
                "ec2:CreateNetworkInterface",
                "ec2:DeleteNetworkInterface",
                "ec2:ModifyNetworkInterfaceAttribute",
            ],
            "*",
            {"StringEquals": {"aws:RequestedRegion": "ap-northeast-2"}},
        ),
        permission(
            [
                "iam:CreateRole",
                "iam:GetRole",
                "iam:DeleteRole",
                "iam:UpdateAssumeRolePolicy",
                "iam:PutRolePolicy",
                "iam:GetRolePolicy",
                "iam:DeleteRolePolicy",
                "iam:TagRole",
                "iam:UntagRole",
                "iam:CreateInstanceProfile",
                "iam:GetInstanceProfile",
                "iam:AddRoleToInstanceProfile",
                "iam:RemoveRoleFromInstanceProfile",
                "iam:DeleteInstanceProfile",
                "iam:PassRole",
            ],
            roles,
        ),
        permission(
            [
                "rds:CreateDBInstance",
                "rds:DeleteDBInstance",
                "rds:ModifyDBInstance",
                "rds:CreateDBSubnetGroup",
                "rds:DeleteDBSubnetGroup",
                "rds:ModifyDBSubnetGroup",
                "rds:CreateDBParameterGroup",
                "rds:ModifyDBParameterGroup",
                "rds:DeleteDBParameterGroup",
                "rds:AddTagsToResource",
                "rds:RemoveTagsFromResource",
                "rds:CreateDBSnapshot",
            ],
            f"arn:aws:rds:ap-northeast-2:{account}:*:ikonly-demo-*",
        ),
        permission(["rds:Describe*", "rds:ListTagsForResource"], "*"),
        permission(
            [
                "ecr:CreateRepository",
                "ecr:DeleteRepository",
                "ecr:DescribeRepositories",
                "ecr:GetRepositoryPolicy",
                "ecr:SetRepositoryPolicy",
                "ecr:PutImageScanningConfiguration",
                "ecr:PutImageTagMutability",
                "ecr:TagResource",
                "ecr:UntagResource",
            ],
            f"arn:aws:ecr:ap-northeast-2:{account}:repository/ikonly-demo/*",
        ),
        permission(
            [
                "ssm:CreateDocument",
                "ssm:UpdateDocument",
                "ssm:UpdateDocumentDefaultVersion",
                "ssm:DescribeDocument",
                "ssm:GetDocument",
                "ssm:DeleteDocument",
                "ssm:AddTagsToResource",
                "ssm:RemoveTagsFromResource",
                "ssm:ListTagsForResource",
            ],
            [ssm + "document/IkonlyDeploy"],
        ),
        permission(
            ["ssm:GetParameter", "ssm:GetParameters"],
            ssm + "parameter/multi-agent-autohealing/ikonly-demo/operations/master-password",
        ),
        permission(
            ["kms:Decrypt"],
            f"arn:aws:kms:ap-northeast-2:{account}:key/*",
            {"StringEquals": {"kms:ViaService": "ssm.ap-northeast-2.amazonaws.com"}},
        ),
        permission(
            [
                "logs:CreateLogGroup",
                "logs:DeleteLogGroup",
                "logs:PutRetentionPolicy",
                "logs:TagLogGroup",
                "logs:UntagLogGroup",
                "logs:ListTagsLogGroup",
                "logs:TagResource",
                "logs:UntagResource",
                "logs:ListTagsForResource",
            ],
            [
                f"arn:aws:logs:ap-northeast-2:{account}:log-group:/ikonly-demo/*",
                f"arn:aws:logs:ap-northeast-2:{account}:log-group:/aws/rds/instance/ikonly-demo-postgres/*",
            ],
        ),
        permission(["logs:DescribeLogGroups", "cloudwatch:DescribeAlarms"], "*"),
        permission(
            [
                "cloudwatch:PutMetricAlarm",
                "cloudwatch:DeleteAlarms",
                "cloudwatch:TagResource",
                "cloudwatch:UntagResource",
                "cloudwatch:ListTagsForResource",
            ],
            f"arn:aws:cloudwatch:ap-northeast-2:{account}:alarm:ikonly-demo-*",
        ),
        permission(
            [
                "sns:CreateTopic",
                "sns:DeleteTopic",
                "sns:SetTopicAttributes",
                "sns:GetTopicAttributes",
                "sns:Subscribe",
                "sns:ListSubscriptionsByTopic",
                "sns:TagResource",
                "sns:UntagResource",
                "sns:ListTagsForResource",
            ],
            f"arn:aws:sns:ap-northeast-2:{account}:ikonly-demo-*",
        ),
        permission(["sns:Unsubscribe"], f"arn:aws:sns:ap-northeast-2:{account}:ikonly-demo-*:*"),
        permission(
            [
                "s3:CreateBucket",
                "s3:DeleteBucket",
                "s3:PutBucketPolicy",
                "s3:GetBucketPolicy",
                "s3:DeleteBucketPolicy",
                "s3:PutBucketPublicAccessBlock",
                "s3:GetBucketPublicAccessBlock",
                "s3:PutEncryptionConfiguration",
                "s3:GetEncryptionConfiguration",
                "s3:PutLifecycleConfiguration",
                "s3:GetLifecycleConfiguration",
                "s3:PutBucketTagging",
                "s3:GetBucketTagging",
                "s3:GetBucketLocation",
            ],
            "arn:aws:s3:::ikonly-demo-observability-evidence-*",
        ),
    ]
    return {
        "controller": {"Version": "2012-10-17", "Statement": controller},
        "cloudformation": {"Version": "2012-10-17", "Statement": cloudformation},
    }


def bootstrap(config, output, *, apply=False):
    aws = Aws(config["region"])
    preflight(aws, config)
    repo = json.loads(run(["gh", "api", "repos/" + REPO]))
    subject = json.loads(run(["gh", "api", "repos/" + REPO + "/actions/oidc/customization/sub"]))
    if not repo.get("permissions", {}).get("admin") or subject.get("use_default") is not True:
        raise ValueError("Repository admin and default OIDC subject required")
    prefix = subject.get("sub_claim_prefix")
    expected = (
        f"repo:ik-e@{repo['owner']['id']}/multi-agent-autohealing@{repo['id']}"
        if subject.get("use_immutable_subject")
        else "repo:" + REPO
    )
    if prefix != expected:
        raise ValueError("Repository OIDC subject prefix differs from confirmed IDs")
    provider = (
        f"arn:aws:iam::{config['account_id']}:oidc-provider/token.actions.githubusercontent.com"
    )
    item = aws("iam", "get-open-id-connect-provider", "--open-id-connect-provider-arn", provider)
    if (
        item["Url"] != "token.actions.githubusercontent.com"
        or "sts.amazonaws.com" not in item["ClientIDList"]
    ):
        raise ValueError("Existing shared GitHub OIDC provider configuration mismatch")
    statements = policies(config["account_id"])
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Federated": provider},
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {
                    "StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                        "token.actions.githubusercontent.com:sub": expected
                        + ":ref:refs/heads/ikonly",
                    }
                },
            }
        ],
    }
    cf_trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "cloudformation.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    record = {
        "account_id": config["account_id"],
        "subject": expected + ":ref:refs/heads/ikonly",
        "provider_reused": provider,
        "roles": {
            "ikonly-control-controller": {"trust": trust, "policy": statements["controller"]},
            "ikonly-control-cloudformation": {
                "trust": cf_trust,
                "policy": statements["cloudformation"],
            },
        },
        "operation_config": {
            **config,
            "cloudformation_role_arn": f"arn:aws:iam::{config['account_id']}:role/ikonly-control-cloudformation",
        },
        "applied": False,
    }
    write(output, record)
    write(Path(output).parent / "operation-config.json", record["operation_config"])
    if not apply:
        return record
    existing = {x["RoleName"]: x for x in aws("iam", "list-roles")["Roles"]}
    for name, value in record["roles"].items():
        if name in existing:
            tags = {
                x["Key"]: x["Value"]
                for x in aws("iam", "list-role-tags", "--role-name", name)["Tags"]
            }
            if tags.get("ManagedBy") != "ikonly-control" or tags.get("Repository") != REPO:
                raise ValueError("Existing control role is not owned by this repository")
            aws("iam", "update-role", "--role-name", name, "--max-session-duration", "7200")
            aws(
                "iam",
                "update-assume-role-policy",
                payload={"RoleName": name, "PolicyDocument": json.dumps(value["trust"])},
            )
        else:
            aws(
                "iam",
                "create-role",
                payload={
                    "RoleName": name,
                    "AssumeRolePolicyDocument": json.dumps(value["trust"]),
                    "MaxSessionDuration": 7200,
                    "Tags": CONTROL_TAGS,
                },
            )
        aws(
            "iam",
            "put-role-policy",
            payload={
                "RoleName": name,
                "PolicyName": "IkonlyLifecycle",
                "PolicyDocument": json.dumps(value["policy"]),
            },
        )
    for key, value in {
        "IKONLY_DEPLOY_ROLE_ARN": f"arn:aws:iam::{config['account_id']}:role/ikonly-control-controller",
        "IKONLY_ACCOUNT_ID": config["account_id"],
        "IKONLY_CONFIG": json.dumps(
            {
                **config,
                "cloudformation_role_arn": f"arn:aws:iam::{config['account_id']}:role/ikonly-control-cloudformation",
            },
            separators=(",", ":"),
        ),
    }.items():
        run(["gh", "variable", "set", key, "--repo", REPO, "--body", value])
    record["applied"] = True
    write(output, record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    bootstrap(
        validate_config(json.loads(args.config.read_text(encoding="utf-8"))),
        args.output,
        apply=args.apply,
    )
    print("OIDC control plan recorded; shared provider preserved")


if __name__ == "__main__":
    main()
