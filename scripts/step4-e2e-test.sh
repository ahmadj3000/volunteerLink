#!/usr/bin/env bash
# Step 4 end-to-end test with a fake application row (no frontend / NGO API needed).
#   bash scripts/step4-e2e-test.sh start  <id>   -> insert pending row, show workflow + parked token
#   bash scripts/step4-e2e-test.sh decide <id> accepted|rejected   -> simulate the NGO decision
#   bash scripts/step4-e2e-test.sh dup    <id>   -> start a 2nd execution: must end in "Duplicate"
#   bash scripts/step4-e2e-test.sh clean  <id>   -> delete the row
export AWS_REGION=us-east-1 AWS_PAGER=""
SM=arn:aws:states:us-east-1:170813308606:stateMachine:vl-application-workflow
CMD=$1; ID=${2:-e2e-opp1_e2e-vol1}
START=$(date -u -d '+3 days' +%Y-%m-%dT09:00:00+00:00)
show(){
  aws dynamodb get-item --table-name vl-applications --key "{\"applicationId\":{\"S\":\"$ID\"}}" --consistent-read \
    --query 'Item.{status:status.S,wf:workflowExecution.S,tokenParked:taskToken.S!=null}'
  aws stepfunctions list-executions --state-machine-arn $SM --max-results 3 \
    --query 'executions[].{name:name,status:status,start:startDate}' --output table
}
case $CMD in
start)
  aws pipes describe-pipe --name vl-applications-pipe --query CurrentState --output text
  aws dynamodb put-item --table-name vl-applications --condition-expression "attribute_not_exists(applicationId)" --item "{
   \"applicationId\":{\"S\":\"$ID\"},\"opportunityId\":{\"S\":\"${ID%%_*}\"},\"volunteerId\":{\"S\":\"${ID#*_}\"},
   \"ngoId\":{\"S\":\"e2e-ngo\"},\"status\":{\"S\":\"pending\"},\"opportunityTitle\":{\"S\":\"E2E Food Drive\"},
   \"opportunityStartAt\":{\"S\":\"$START\"},\"appliedAt\":{\"S\":\"$(date -u +%Y-%m-%dT%H:%M:%S+00:00)\"},
   \"ngoContactEmail\":{\"S\":\"ahmadj3100+ngo@gmail.com\"},\"volunteerEmail\":{\"S\":\"ahmadj3100@gmail.com\"}}" && echo "inserted $ID (event $START)"
  echo "waiting 25s..."; sleep 25; show ;;
decide)
  aws dynamodb update-item --table-name vl-applications --key "{\"applicationId\":{\"S\":\"$ID\"}}" \
    --update-expression "SET #s = :d, decidedAt = :now" --condition-expression "#s = :p" \
    --expression-attribute-names '{"#s":"status"}' \
    --expression-attribute-values "{\":d\":{\"S\":\"$3\"},\":p\":{\"S\":\"pending\"},\":now\":{\"S\":\"$(date -u +%FT%TZ)\"}}" && echo "decided $3"
  echo "waiting 25s..."; sleep 25; show
  ARN=$(aws dynamodb get-item --table-name vl-applications --key "{\"applicationId\":{\"S\":\"$ID\"}}" --query Item.workflowExecution.S --output text)
  aws stepfunctions get-execution-history --execution-arn "$ARN" --reverse-order --max-results 8 \
    --query 'events[].[type, stateEnteredEventDetails.name || stateExitedEventDetails.name]' --output text ;;
dup)
  aws stepfunctions start-execution --state-machine-arn $SM --input "{\"applicationId\":\"$ID\",\"opportunityStartAt\":\"$START\"}" --query executionArn --output text > /tmp/dup
  sleep 5; aws stepfunctions get-execution-history --execution-arn "$(cat /tmp/dup)" --reverse-order --max-results 3 \
    --query 'events[].[type, stateEnteredEventDetails.name || stateExitedEventDetails.name]' --output text ;;
clean)
  aws dynamodb delete-item --table-name vl-applications --key "{\"applicationId\":{\"S\":\"$ID\"}}" && echo "deleted $ID" ;;
esac
