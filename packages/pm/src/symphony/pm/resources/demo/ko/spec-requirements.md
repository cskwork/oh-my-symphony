# 휴가 신청

## ADDED Requirements

### Requirement: 휴가를 신청한다
<!-- symphony:req={{ID:submit_request}} -->

시스템은 유효한 휴가 신청을 Pending(승인 대기)으로 저장해야 합니다(SHALL). 같은 직원의 Pending(승인 대기) 또는 Approved(승인) 신청과 날짜가 겹치면 반드시 거절해야 합니다(MUST). 시작일은 종료일보다 늦을 수 없으며, 시작일과 종료일을 모두 휴가에 포함해야 합니다(MUST).

#### Scenario: 유효한 신청을 승인 대기로 저장한다
<!-- symphony:scenario={{ID:request_pending}} -->
- **GIVEN** 로그인한 직원에게 2026-11-02부터 2026-11-03까지 예약된 휴가가 없다
- **WHEN** 직원이 해당 날짜로 휴가를 신청한다
- **THEN** 시스템은 신청 한 건을 Pending(승인 대기)으로 저장하고 날짜와 상태를 보여준다

#### Scenario: 날짜가 겹치는 신청을 거절한다
<!-- symphony:scenario={{ID:overlap_refused}} -->
- **GIVEN** 직원에게 2026-11-02부터 2026-11-03까지 Pending(승인 대기) 신청이 있다
- **WHEN** 직원이 2026-11-03부터 2026-11-04까지 추가로 신청한다
- **THEN** 시스템은 겹치는 신청을 거절하고 새 신청을 저장하지 않으며 날짜를 바꾸도록 안내한다

### Requirement: 신청을 승인하거나 반려한다
<!-- symphony:req={{ID:decide_request}} -->

시스템은 관리자가 자기 팀 직원의 Pending(승인 대기) 신청만 승인하거나 반려할 수 있도록 해야 합니다(SHALL). 승인하면 상태를 Approved(승인)로 바꾸고 직원에게 알려야 합니다(MUST). 반려하려면 비어 있지 않은 사유가 있어야 하며 상태를 Declined(반려)로 바꿔야 합니다(MUST).

#### Scenario: 승인 결과를 직원에게 알린다
<!-- symphony:scenario={{ID:approval_notifies}} -->
- **GIVEN** 관리자와 같은 팀 직원의 Pending(승인 대기) 신청이 있다
- **WHEN** 관리자가 신청을 승인한다
- **THEN** 신청 상태가 Approved(승인)로 바뀌고 직원이 승인 알림을 받는다

#### Scenario: 반려 사유가 없으면 저장하지 않는다
<!-- symphony:scenario={{ID:decline_reason_required}} -->
- **GIVEN** 관리자와 같은 팀 직원의 Pending(승인 대기) 신청이 있다
- **WHEN** 관리자가 사유 없이 반려하려 한다
- **THEN** 신청은 Pending(승인 대기)으로 남고 검토함은 반려를 저장하기 전에 사유 입력을 요청한다
