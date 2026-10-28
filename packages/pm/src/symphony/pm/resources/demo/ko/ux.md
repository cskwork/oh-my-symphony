# 휴가 신청 사용자 흐름

## Actors and journeys
직원이 휴가 날짜를 신청하면 소속 팀의 관리자가 처리합니다. 팀원들은 업무를 계획하기 전에 승인된 휴가를 확인합니다.

## Screen inventory
- /leave/new: 휴가 신청 화면.
- /leave/inbox: 관리자가 승인 대기 신청을 검토하는 화면.
- /team/calendar: 로그인한 사용자의 팀에서 승인된 휴가를 보는 달력.

## Loading, empty, error, and permission states
저장하거나 불러오는 중임을 표시합니다. 검토함이 비어 있으면 승인 대기 신청이 없다고 안내하고, 달력이 비어 있으면 승인된 휴가가 없다고 안내합니다. 오류가 나도 입력한 날짜와 반려 사유는 유지합니다. 로그인하지 않은 사용자는 로그인을 안내하고, 다른 팀의 신청을 처리하려는 관리자에게는 권한이 없다고 알립니다.

## Responsive and accessibility behavior
좁은 화면에서는 입력 항목을 세로로 배치합니다. 날짜 입력란과 버튼에 화면 읽기 도구가 읽을 수 있는 이름(레이블)을 붙이고, 입력 오류가 나면 해당 항목으로 초점을 옮기며, 저장 결과를 화면 읽기 도구에 알립니다. 달력에는 직원 이름과 날짜를 글자로 표시해 색상만으로 구분하지 않습니다.

## Storyboard

### Storyboard: 직원이 휴가를 신청한다
<!-- symphony:storyboard={{ID:request_form}} -->
- Screen/route: /leave/new
- Actor/goal: 직원이 이틀의 휴가를 신청한다.
- Initial state: 로그인했으며 선택한 기간에 예약된 휴가가 없다.
- UI: 시작일, 종료일, 신청 버튼, 처리 상태 안내.
- User action: 2026-11-02부터 2026-11-03까지 선택하고 신청 버튼을 누른다.
- System response: 신청을 Pending(승인 대기)으로 저장하고 날짜와 상태를 보여준다.
- Success: 직원이 접수 결과를 확인하고 관리자의 처리를 기다린다는 것을 알 수 있다.
- Alternate: 제출 전 날짜를 바꾼다. 이때는 신청이 아직 저장되지 않는다.
- Failure: 날짜가 겹치거나 잘못되면 입력 항목에 오류를 표시한다. 저장에 실패하면 입력한 날짜를 유지하고 다시 시도할 수 있다.
- Requirement references: {{ID:submit_request}}, {{ID:request_pending}}, {{ID:overlap_refused}}
- Design references: {{ID:submit_leave}}

### Storyboard: 관리자가 검토함에서 신청을 처리한다
<!-- symphony:storyboard={{ID:manager_inbox}} -->
- Screen/route: /leave/inbox
- Actor/goal: 관리자가 자기 팀 직원의 신청을 검토한다.
- Initial state: 팀 직원의 Pending(승인 대기) 신청이 있다.
- UI: 직원 이름, 휴가 날짜, 승인 버튼, 반려 버튼, 반려 사유 입력란.
- User action: 날짜를 확인하고 승인 버튼을 누른다.
- System response: Approved(승인) 상태로 저장하고 직원에게 알린다.
- Success: 신청이 승인 대기 목록에서 사라지고 처리 결과가 표시된다.
- Alternate: 사유를 입력하고 반려 버튼을 누르면 신청이 Declined(반려)로 바뀐다.
- Failure: 반려 사유가 없으면 신청은 Pending(승인 대기)으로 남는다. 이미 처리했거나 다른 팀의 신청이면 처리를 거절하고 목록을 새로 불러온다.
- Requirement references: {{ID:decide_request}}, {{ID:approval_notifies}}, {{ID:decline_reason_required}}
- Design references: {{ID:decide_leave}}

### Storyboard: 팀원이 달력을 확인한다
<!-- symphony:storyboard={{ID:team_calendar}} -->
- Screen/route: /team/calendar
- Actor/goal: 팀원이 업무를 계획하기 전에 누가 자리를 비우는지 확인한다.
- Initial state: 사용자와 같은 팀의 휴가 신청이 승인되었다.
- UI: 신청 화면의 날짜 선택 기능을 재사용한 월 이동, 직원 이름과 날짜가 적힌 승인 휴가 표시.
- User action: 2026년 11월 달력을 연다.
- System response: 자기 팀의 Approved(승인) 휴가만 불러와 해당 기간의 모든 날짜에 표시한다.
- Success: 승인된 휴가를 확인할 수 있으며 승인 대기와 반려 신청은 보이지 않는다.
- Alternate: 승인된 휴가가 없는 달에는 빈 상태 안내를 보여준다.
- Failure: 불러오기에 실패하면 다시 시도 버튼을 보여주고 이전 자료를 최신 정보처럼 표시하지 않는다.
- Requirement references: {{ID:approved_calendar}}, {{ID:calendar_approved_only}}
- Design references: {{ID:decide_leave}}
