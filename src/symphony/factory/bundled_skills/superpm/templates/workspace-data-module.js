/* templates/workspace-data-module.js — 모듈 데이터 1파일 스캐폴드 (도메인 기능별 분리)
   배치: <project>/data/NN-<module>.js. 모듈마다 registerModule({...}) 한 번 호출.
   data/_common.js(workspace-data-common.js) 다음, app 로직 전에 <script src>로 로드된다.
   - 콜아웃 번호 calloutNo는 화면별 1..N 연속 정수(검증 MISSING_CALLOUT). Figma 서브코드(02-1 등)는 area에 보존.
   - 각 element의 calloutNo는 preview 안 data-callout="N" 요소와 1:1 → 번호 오버레이 자동.
   - 화면 이동은 preview에서 data-nav="<screen_id>".
   - nav 설정 화면은 좌측 메뉴 앱셸 자동 래핑, nav:null 은 풀스크린(팝업/모바일/학생측).
   - 검증 GREEN 조건: page/action/decision 노드 linkedSpecIds≥1·실재 spec / page 노드 id=화면 flowNodeId /
     element.linkedSpecId 실재 / calloutNo 1..N / exception 있는 spec은 element.exception으로 커버. */
registerModule({
  id: "sample", label: "샘플 모듈", order: 1,
  features: [{ id: "FEAT-SMP", title: "샘플" }],
  specs: [
    { id:"SPEC-SMP-01", featureId:"FEAT-SMP", title:"목록 조회·필터",
      input:"검색어·필터", output:"조건에 맞는 목록", businessRule:"정렬·페이지 규칙",
      exception:"무결과 시 빈 상태, 로드 실패 시 토스트" }
  ],
  screens: [
    { id:"SMP_01_01", title:"샘플 목록", nav:"home", device:"web", state:"normal",
      prev:null, next:null, flowNodeId:"FL-SMP-01", mode:"B",
      elements:[
        {calloutNo:1, area:"01 검색", selector:"[data-callout='1']", behavior:"입력 후 목록 필터",
         dataContract:"플레이스홀더 '검색'", exception:"무결과 시 빈 상태", linkedSpecId:"SPEC-SMP-01"},
        {calloutNo:2, area:"02 목록", selector:".lst", behavior:"행 클릭 시 상세",
         dataContract:"컬럼: 이름·상태·일시", exception:"로드 실패 시 토스트", linkedSpecId:"SPEC-SMP-01"}
      ],
      preview:[
        "<div class='scr'><style>.lst{width:100%;border-collapse:collapse;font-size:12px}",
        ".lst th,.lst td{border-bottom:1px solid #eef0f2;padding:6px;text-align:left}.lst th{background:#f8fafc}",
        "input{font-size:12px;padding:5px 8px;border:1px solid #d1d5db;border-radius:6px;margin-bottom:8px}</style>",
        "<input data-callout='1' placeholder='검색'>",
        "<table class='lst' data-callout='2'><thead><tr><th>이름</th><th>상태</th><th>일시</th></tr></thead>",
        "<tbody><tr><td>항목 A</td><td>정상</td><td>26.06.22.</td></tr><tr><td>항목 B</td><td>대기</td><td>26.06.21.</td></tr></tbody></table>",
        "</div>"
      ].join("")
    }
  ],
  flow: {
    nodes: [
      { id:"FL-SMP-01", type:"page", label:"샘플 목록", linkedSpecIds:["SPEC-SMP-01"], entry:true, entryLabel:"샘플" }
    ],
    edges: []
  }
});
