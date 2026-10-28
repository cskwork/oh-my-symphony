# 팀 휴가 신청

## Problem
직원들이 메신저로 휴가를 신청하면 관리자는 요청을 놓치기 쉽습니다. 팀원들도 누가 언제 자리를 비우는지 정확히 확인하기 어렵습니다.

## Intended users
휴가를 신청하는 직원, 자기 팀의 신청을 검토하는 관리자, 승인된 휴가를 참고해 업무를 계획하는 팀원입니다.

## Desired outcome
직원이 휴가 날짜를 한 번 입력하고 처리 결과를 확인하며, 승인된 휴가를 팀 달력에서 볼 수 있습니다.

## Scope
휴가 신청 화면, 관리자 검토함, 팀 달력을 만듭니다. 신청과 처리 결과를 저장하고, 날짜가 겹치는 신청을 거절하며, 승인 후 직원에게 알립니다.

## Non-goals
급여 처리, 휴가 발생 일수 계산, 공휴일 관리, 연차 잔여 일수 변경은 포함하지 않습니다. 신청 화면에 잔여 일수를 보여줄지는 아직 결정되지 않은 별도 사항입니다.

## Constraints
기존 직원 로그인과 팀 소속 정보를 사용합니다. 휴가는 하루 단위로 신청하며, 시작일과 종료일을 모두 휴가 기간에 포함합니다. 직원이 속한 팀의 관리자만 신청을 승인하거나 반려할 수 있습니다.

## Success criteria
유효한 신청은 Pending(승인 대기)으로 저장됩니다. 날짜가 겹치는 신청은 거절됩니다. 승인하면 직원에게 알리고, 반려하려면 사유가 필요하며, 팀 달력에는 승인된 휴가만 표시됩니다.

## Assumptions
직원은 한 팀에 소속되고 팀에는 관리자가 있습니다. 기존 알림 수단으로 승인 소식을 전달할 수 있습니다. Pending(승인 대기)과 Approved(승인) 상태의 신청은 해당 날짜를 예약하며, Declined(반려) 상태는 예약하지 않습니다.

## Open decisions

### Decision: 관리자는 자기 팀의 휴가 신청을 승인한다
<!-- symphony:decision={{ID:manager_scope}} -->
- Owner: 제품 담당자
- Impact: Medium
- Status: Accepted
- Reason: 팀의 인력 상황을 아는 관리자가 자기 팀의 신청만 처리하도록 합니다.

### Decision: 신청 화면에 잔여 휴가 일수를 보여준다
<!-- symphony:decision={{ID:balance_display}} -->
- Owner: 인사 담당자
- Impact: Low
- Status: Unresolved
- Reason: 인사 담당자가 믿을 수 있는 잔여 일수 조회 방법을 아직 확인하지 못했습니다. 결정 전에는 날짜와 신청 상태만으로 화면을 제공할 수 있습니다.

## Affected capabilities
- requirements
- team-calendar
