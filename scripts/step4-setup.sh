#!/usr/bin/env bash
# Step 4 (application workflow) setup, Volunteer account 170813308606, us-east-1.
# Run from the repo root in CloudShell. Re-runnable: "already exists" errors are ignored.
set -uo pipefail
ACC=170813308606; R=us-east-1; export AWS_REGION=$R AWS_PAGER=""
W=infra/workflow
ok(){ echo "OK  $*"; }; step(){ echo; echo "=== $*"; }
quiet(){ "$@" >/dev/null 2>/tmp/err || { grep -qiE "exist|conflict" /tmp/err && echo "    (already there)" || { cat /tmp/err; exit 1; }; }; }

step "1. email worker code + config"
( cd backend/email_worker && rm -f /tmp/worker.zip && zip -q /tmp/worker.zip handler.py )
aws lambda update-function-code --function-name vl-email-worker --zip-file fileb:///tmp/worker.zip >/dev/null && ok code
aws lambda wait function-updated --function-name vl-email-worker
aws lambda update-function-configuration --function-name vl-email-worker --handler handler.handler --timeout 10 \
  --environment "Variables={APPLICATIONS_TABLE=vl-applications,SENDER_EMAIL=ahmadj3100+vl-noreply@gmail.com}" >/dev/null && ok config
aws lambda wait function-updated --function-name vl-email-worker
WROLE=$(aws lambda get-function-configuration --function-name vl-email-worker --query Role --output text | awk -F/ '{print $NF}')
aws iam attach-role-policy --role-name "$WROLE" --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaSQSQueueExecutionRole && ok "sqs managed policy on $WROLE"
aws iam put-role-policy --role-name "$WROLE" --policy-name vl-email-worker-access --policy-document file://$W/email-worker-policy.json && ok "inline policy"
quiet aws logs create-log-group --log-group-name /aws/lambda/vl-email-worker
aws logs put-retention-policy --log-group-name /aws/lambda/vl-email-worker --retention-in-days 14 && ok "log retention 14d"

step "2. events DLQ"
EDLQ_URL=$(aws sqs create-queue --queue-name vl-events-dlq --attributes MessageRetentionPeriod=1209600,SqsManagedSseEnabled=true --query QueueUrl --output text)
EDLQ=arn:aws:sqs:$R:$ACC:vl-events-dlq; ok "$EDLQ"

step "3. event bus"
quiet aws events create-event-bus --name vl-applications-bus
BUS=arn:aws:events:$R:$ACC:event-bus/vl-applications-bus; ok "$BUS"

step "4. IAM roles"
mkrole(){ # name service policyfile
  quiet aws iam create-role --role-name "$1" --assume-role-policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Principal\":{\"Service\":\"$2\"},\"Action\":\"sts:AssumeRole\",\"Condition\":{\"StringEquals\":{\"aws:SourceAccount\":\"$ACC\"}}}]}"
  aws iam put-role-policy --role-name "$1" --policy-name "$1-access" --policy-document "file://$3" && ok "role $1"
}
STREAM=$(aws dynamodb describe-table --table-name vl-applications --query Table.LatestStreamArn --output text)
sed "s#STREAM_ARN#$STREAM#" $W/pipe-policy.json > /tmp/pipe-policy.json
mkrole vl-applications-pipe-role pipes.amazonaws.com /tmp/pipe-policy.json
mkrole vl-application-workflow-role states.amazonaws.com $W/workflow-policy.json
mkrole vl-decision-relay-role states.amazonaws.com $W/relay-policy.json
mkrole vl-applications-events-role events.amazonaws.com $W/events-policy.json
echo "    waiting 15s for IAM to propagate"; sleep 15

step "5. state machines (+ ERROR logs, 14d)"
mksm(){ # name type definition role
  LG=/aws/vendedlogs/states/$1
  quiet aws logs create-log-group --log-group-name "$LG"
  aws logs put-retention-policy --log-group-name "$LG" --retention-in-days 14
  LOGCFG="{\"level\":\"ERROR\",\"includeExecutionData\":false,\"destinations\":[{\"cloudWatchLogsLogGroup\":{\"logGroupArn\":\"arn:aws:logs:$R:$ACC:log-group:$LG:*\"}}]}"
  SM=arn:aws:states:$R:$ACC:stateMachine:$1
  if aws stepfunctions describe-state-machine --state-machine-arn "$SM" >/dev/null 2>&1; then
    aws stepfunctions update-state-machine --state-machine-arn "$SM" --definition "file://$3" \
      --role-arn "arn:aws:iam::$ACC:role/$4" --logging-configuration "$LOGCFG" >/dev/null && ok "updated $1"
  else
    aws stepfunctions create-state-machine --name "$1" --type "$2" --definition "file://$3" \
      --role-arn "arn:aws:iam::$ACC:role/$4" --logging-configuration "$LOGCFG" >/dev/null && ok "created $1"
  fi
}
mksm vl-application-workflow STANDARD $W/application-workflow.asl.json vl-application-workflow-role
mksm vl-decision-relay EXPRESS $W/decision-relay.asl.json vl-decision-relay-role

step "6. pipe: stream -> bus"
FILTERS=$(python3 -c 'import json;print(json.dumps({"Filters":[{"Pattern":json.dumps(p)} for p in [
 {"eventName":["INSERT"],"dynamodb":{"NewImage":{"status":{"S":["pending"]}}}},
 {"eventName":["MODIFY"],"dynamodb":{"OldImage":{"status":{"S":["pending"]}},"NewImage":{"status":{"S":["accepted","rejected"]}}}}]]}))')
SRC="{\"DynamoDBStreamParameters\":{\"StartingPosition\":\"LATEST\",\"BatchSize\":1,\"MaximumRetryAttempts\":3,\"DeadLetterConfig\":{\"Arn\":\"$EDLQ\"}},\"FilterCriteria\":$FILTERS}"
TGT='{"EventBridgeEventBusParameters":{"DetailType":"ApplicationChanged","Source":"volunteerlink.applications"}}'
quiet aws pipes create-pipe --name vl-applications-pipe --role-arn arn:aws:iam::$ACC:role/vl-applications-pipe-role \
  --source "$STREAM" --source-parameters "$SRC" --target "$BUS" --target-parameters "$TGT" --desired-state RUNNING
ok "pipe vl-applications-pipe"

step "7. rules: created -> workflow, decided -> relay"
EROLE=arn:aws:iam::$ACC:role/vl-applications-events-role
aws events put-rule --name vl-application-created --event-bus-name vl-applications-bus \
  --event-pattern '{"source":["volunteerlink.applications"],"detail":{"eventName":["INSERT"]}}' >/dev/null
aws events put-rule --name vl-application-decided --event-bus-name vl-applications-bus \
  --event-pattern '{"source":["volunteerlink.applications"],"detail":{"eventName":["MODIFY"]}}' >/dev/null
RETRY='{"MaximumRetryAttempts":10,"MaximumEventAgeInSeconds":3600}'
aws events put-targets --rule vl-application-created --event-bus-name vl-applications-bus --targets "[{
  \"Id\":\"workflow\",\"Arn\":\"arn:aws:states:$R:$ACC:stateMachine:vl-application-workflow\",\"RoleArn\":\"$EROLE\",
  \"InputTransformer\":{\"InputPathsMap\":{\"id\":\"\$.detail.dynamodb.NewImage.applicationId.S\",\"start\":\"\$.detail.dynamodb.NewImage.opportunityStartAt.S\"},
    \"InputTemplate\":\"{\\\"applicationId\\\": <id>, \\\"opportunityStartAt\\\": <start>}\"},
  \"RetryPolicy\":$RETRY,\"DeadLetterConfig\":{\"Arn\":\"$EDLQ\"}}]" >/dev/null && ok "created rule"
aws events put-targets --rule vl-application-decided --event-bus-name vl-applications-bus --targets "[{
  \"Id\":\"relay\",\"Arn\":\"arn:aws:states:$R:$ACC:stateMachine:vl-decision-relay\",\"RoleArn\":\"$EROLE\",
  \"InputPath\":\"\$.detail.dynamodb.NewImage\",\"RetryPolicy\":$RETRY,\"DeadLetterConfig\":{\"Arn\":\"$EDLQ\"}}]" >/dev/null && ok "decided rule"
# let the rules drop undeliverable events into the DLQ
aws sqs set-queue-attributes --queue-url "$EDLQ_URL" --attributes "{\"Policy\":\"{\\\"Version\\\":\\\"2012-10-17\\\",\\\"Statement\\\":[{\\\"Effect\\\":\\\"Allow\\\",\\\"Principal\\\":{\\\"Service\\\":\\\"events.amazonaws.com\\\"},\\\"Action\\\":\\\"sqs:SendMessage\\\",\\\"Resource\\\":\\\"$EDLQ\\\",\\\"Condition\\\":{\\\"ArnLike\\\":{\\\"aws:SourceArn\\\":\\\"arn:aws:events:$R:$ACC:rule/vl-applications-bus/*\\\"}}}]}\"}" && ok "DLQ policy"

step "8. worker trigger (last, so nothing is consumed before the worker is ready)"
if [ "$(aws lambda list-event-source-mappings --function-name vl-email-worker --query 'length(EventSourceMappings)')" = "0" ]; then
  aws lambda create-event-source-mapping --function-name vl-email-worker \
    --event-source-arn arn:aws:sqs:$R:$ACC:vl-email-queue --batch-size 5 \
    --function-response-types ReportBatchItemFailures --scaling-config MaximumConcurrency=2 >/dev/null && ok "SQS trigger"
else echo "    (trigger already there)"; fi
echo; echo "ALL DONE"
