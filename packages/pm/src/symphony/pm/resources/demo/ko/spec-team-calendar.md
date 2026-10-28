# 팀 달력

## ADDED Requirements

### Requirement: 승인된 휴가를 팀 달력에 보여준다
<!-- symphony:req={{ID:approved_calendar}} -->

시스템은 직원의 Approved(승인) 휴가를 소속 팀 달력에 표시하고 시작일부터 종료일까지 모두 포함해야 합니다(SHALL). Pending(승인 대기)과 Declined(반려) 신청 및 다른 팀의 신청은 반드시 제외해야 합니다(MUST).

#### Scenario: 자기 팀의 승인된 휴가만 표시한다
<!-- symphony:scenario={{ID:calendar_approved_only}} -->
- **GIVEN** 팀에 2026-11-02의 Approved(승인) 휴가와 별도의 Pending(승인 대기) 및 Declined(반려) 신청이 있고 다른 팀에도 Approved(승인) 휴가가 있다
- **WHEN** 로그인한 팀원이 자기 팀의 11월 달력을 연다
- **THEN** 달력은 자기 팀의 Approved(승인) 휴가를 2026-11-02에 표시하고 나머지 신청은 모두 제외한다
