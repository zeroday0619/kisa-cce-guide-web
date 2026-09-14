const assert = require("node:assert/strict");
const test = require("node:test");

require("../site/assets/search-core.js");

const {analyzeQuery, rankRecords} = globalThis.KisaNaturalSearch;

const createRecord = ({
  code,
  title,
  domainIdentifier,
  domainLabel,
  categoryLabel,
  searchableText,
  searchSections = {},
  exactTerms = [],
  targetIdentifiers = ["linux"],
  targetLabels = ["Linux"],
  severityLevel = "high",
  order,
}) => ({
  code,
  title,
  domainIdentifier,
  domainLabel,
  categoryLabel,
  searchSections: {
    inspection: searchableText,
    purpose: "",
    threat: "",
    judgment: "",
    action: "",
    impact: "",
    guidance: "",
    reference: "",
    ...searchSections,
  },
  exactTerms,
  targetIdentifiers,
  targetLabels,
  sourceTargetText: targetLabels.join(", "),
  severityLevel,
  order,
});

const records = [
  createRecord({
    code: "U-01",
    title: "root 계정 원격 접속 제한",
    domainIdentifier: "unix",
    domainLabel: "Unix 서버",
    categoryLabel: "계정 관리",
    searchableText: "Linux root 원격 접속 차단",
    searchSections: {
      guidance:
        "SOLARIS SSH 설정 AIX SSH /etc/ssh/sshd_config에서 PermitRootLogin No 설정",
      reference: "무차별 대입 공격 Brute Force Attack",
    },
    exactTerms: ["PermitRootLogin No", "#pts/0", "# authselect current"],
    targetIdentifiers: ["linux", "aix"],
    targetLabels: ["Linux", "AIX"],
    order: 1,
  }),
  createRecord({
    code: "W-09",
    title: "비밀번호 관리정책 설정",
    domainIdentifier: "windows",
    domainLabel: "Windows 서버",
    categoryLabel: "계정 관리",
    searchableText: "Windows 비밀번호 복잡성 및 변경 주기 정책",
    targetIdentifiers: ["solaris"],
    targetLabels: ["Windows"],
    order: 2,
  }),
  createRecord({
    code: "CA-17",
    title: "로그 보관 기간 설정",
    domainIdentifier: "cloud",
    domainLabel: "클라우드",
    categoryLabel: "운영 관리",
    searchableText: "클라우드 서비스 로그를 기관 정책에 맞게 장기 보관",
    order: 3,
  }),
  createRecord({
    code: "WEB-04",
    title: "디렉터리 리스팅 제거",
    domainIdentifier: "web-service",
    domainLabel: "웹 서비스",
    categoryLabel: "서비스 관리",
    searchableText: "웹 서버 디렉터리 파일 목록 노출 차단",
    order: 4,
  }),
  createRecord({
    code: "SI",
    title: "SQL 인젝션",
    domainIdentifier: "web-application",
    domainLabel: "Web Application(웹)",
    categoryLabel: "SQL 인젝션",
    searchableText: "웹 애플리케이션 SQL 삽입 공격 차단",
    searchSections: {
      guidance: "Prepared Statement로 사용자 입력과 SQL 쿼리를 분리",
    },
    order: 5,
  }),
  createRecord({
    code: "D-26",
    title: "DBMS 감사로그 설정",
    domainIdentifier: "dbms",
    domainLabel: "DBMS",
    categoryLabel: "로그 관리",
    searchableText: "데이터베이스 변경 내역 감사 기록",
    order: 6,
  }),
  createRecord({
    code: "CA-18",
    title: "백업 사용 여부",
    domainIdentifier: "cloud",
    domainLabel: "클라우드",
    categoryLabel: "운영 관리",
    searchableText: "클라우드 장애 발생 시 복구를 위한 백업",
    order: 7,
  }),
  createRecord({
    code: "U-03",
    title: "계정 잠금 임계값 설정",
    domainIdentifier: "unix",
    domainLabel: "Unix 서버",
    categoryLabel: "계정 관리",
    searchableText: "로그인 실패 횟수를 제한하는 계정 잠금 정책",
    order: 8,
  }),
  createRecord({
    code: "WEB-08",
    title: "웹 서비스 파일 업로드 및 다운로드 용량 제한",
    domainIdentifier: "web-service",
    domainLabel: "웹 서비스",
    categoryLabel: "서비스 관리",
    searchableText: "업로드 파일 크기와 다운로드 용량 제한",
    order: 9,
  }),
  createRecord({
    code: "N-11",
    title: "원격로그 서버 사용",
    domainIdentifier: "network-device",
    domainLabel: "네트워크 장비",
    categoryLabel: "로그 관리",
    searchableText: "네트워크 장비 로그를 원격 서버로 전송",
    order: 10,
  }),
  createRecord({
    code: "HV-13",
    title: "ESXi Shell 세션 종료 시간 설정",
    domainIdentifier: "virtualization-device",
    domainLabel: "가상화 장비",
    categoryLabel: "시스템 서비스 관리",
    searchableText: "VMware ESXi Shell 자동 종료 timeout 설정",
    order: 11,
  }),
  createRecord({
    code: "C-19",
    title: "기술지원이 종료된 제품 미사용",
    domainIdentifier: "control-system",
    domainLabel: "제어시스템",
    categoryLabel: "패치 관리",
    searchableText: "지원 종료 운영체제와 소프트웨어를 사용하지 않음",
    order: 12,
  }),
  createRecord({
    code: "EP",
    title: "에러 페이지 적용 미흡",
    domainIdentifier: "web-application",
    domainLabel: "Web Application(웹)",
    categoryLabel: "에러 페이지 적용 미흡",
    searchableText: "웹 에러 페이지에서 스택 트레이스 노출을 차단",
    order: 13,
  }),
  createRecord({
    code: "LOW-01",
    title: "낮은 중요도 예제",
    domainIdentifier: "pc",
    domainLabel: "PC",
    categoryLabel: "보안 관리",
    searchableText: "낮은 중요도 점검",
    severityLevel: "low",
    order: 14,
  }),
  createRecord({
    code: "MEDIUM-01",
    title: "중간 중요도 예제",
    domainIdentifier: "pc",
    domainLabel: "PC",
    categoryLabel: "보안 관리",
    searchableText: "중간 중요도 점검",
    severityLevel: "medium",
    order: 15,
  }),
];

test("matches Linux family aliases against versioned revised distribution targets", () => {
  const revisedRecords = ["rhel-10", "ubuntu-26.04", "debian-13"].map(
    (targetIdentifier, index) => createRecord({
      code: `U-0${index + 1}`,
      title: "계정 설정",
      domainIdentifier: "unix",
      domainLabel: "Unix 서버",
      categoryLabel: "계정 관리",
      searchableText: "계정 설정 여부 점검",
      targetIdentifiers: [targetIdentifier],
      targetLabels: [targetIdentifier],
      order: index + 1,
    }),
  );
  const unrelatedRecord = createRecord({
    code: "W-01",
    title: "계정 설정",
    domainIdentifier: "windows",
    domainLabel: "Windows 서버",
    categoryLabel: "계정 관리",
    searchableText: "계정 설정 여부 점검",
    targetIdentifiers: ["windows"],
    targetLabels: ["Windows"],
    order: 4,
  });
  for (const query of ["Linux", "리눅스", "Ubuntu", "우분투", "Debian"]) {
    assert.deepEqual(
      rankRecords([...revisedRecords, unrelatedRecord], query).map(({record}) => record.code),
      ["U-01", "U-02", "U-03"],
      query,
    );
  }
  assert.ok(rankRecords(records, "Ubuntu").some(({record}) => record.code === "U-01"));
});

const assertTopResult = (query, expectedCode) => {
  const rankedRecords = rankRecords(records, query);
  assert.ok(rankedRecords.length > 0, query);
  assert.equal(rankedRecords[0].record.code, expectedCode, query);
};

test("normalizes Korean particles and request endings", () => {
  const analysis = analyzeQuery("리눅스에서 root 원격 로그인을 막고 싶어");
  assert.deepEqual(
    analysis.units.map((unit) => unit.label),
    ["root", "원격", "로그인", "막고"],
  );
});

test("ranks representative natural-language security queries", () => {
  assertTopResult("리눅스에서 root 원격 로그인을 막고 싶어", "U-01");
  assertTopResult("윈도우 비밀번호 정책", "W-09");
  assertTopResult("로그를 오래 보관하는 항목", "CA-17");
  assertTopResult("로그 보관 기간 조치 시 영향", "CA-17");
  assertTopResult("웹 서버 디렉터리 목록을 숨기고 싶어", "WEB-04");
  assertTopResult("웹에서 SQL 삽입 공격을 막고 싶어", "SI");
  assertTopResult("Prepared Statement로 사용자 입력 분리", "SI");
  assertTopResult("데이터베이스 변경 내역 감사를 남겨야 해", "D-26");
  assertTopResult("클라우드 장애 복구용 백업", "CA-18");
  assertTopResult("유닉스 로그인 실패 횟수 제한", "U-03");
  assertTopResult("웹 업로드 파일 크기를 제한하고 싶어", "WEB-08");
  assertTopResult("네트워크 장비 로그를 원격 서버로 보내기", "N-11");
  assertTopResult("ESXi 셸을 오래 두면 자동 종료", "HV-13");
  assertTopResult("제어시스템 지원 종료 소프트웨어", "C-19");
  assertTopResult("웹 에러에서 스택 트레이스 숨기기", "EP");
});

test("preserves exact code and technical-literal lookup", () => {
  assertTopResult("u01", "U-01");
  assertTopResult("PermitRootLogin No", "U-01");
  const [technicalResult] = rankRecords(records, "PermitRootLogin No");
  assert.equal(technicalResult.contextSection, "guidance");
  assert.match(technicalResult.context, /PermitRootLogin No/);
  const [punctuatedLiteralResult] = rankRecords(records, "#pts/0");
  assert.equal(punctuatedLiteralResult.contextSection, "technicalTerm");
  assert.equal(punctuatedLiteralResult.contextLabel, "설정값");
  assert.equal(punctuatedLiteralResult.context, "#pts/0");
  const [multiTokenLiteralResult] = rankRecords(
    records,
    "# authselect current",
  );
  assert.equal(multiTokenLiteralResult.contextSection, "technicalTerm");
  assert.equal(multiTokenLiteralResult.context, "# authselect current");
  const [platformResult] = rankRecords(
    records,
    "AIX SSH root 접속 차단 설정",
  );
  assert.equal(platformResult.record.code, "U-01");
  assert.match(platformResult.context, /AIX SSH/);
});

test("keeps compatibility with version 1 searchableText records", () => {
  const legacyRecord = {...records[0], searchableText: "root 원격 접속 차단"};
  delete legacyRecord.searchSections;
  const [topResult] = rankRecords([legacyRecord], "root 원격 접속을 막아");
  assert.equal(topResult.record.code, "U-01");
  assert.equal(topResult.contextSection, "inspection");
});

test("returns results for domain, target, and severity intents without text units", () => {
  assertTopResult("윈도우", "W-09");
  assertTopResult("클라우드", "CA-17");
  assertTopResult("ESXi", "HV-13");
  assertTopResult("중요도 하", "LOW-01");
  assertTopResult("중요도 중", "MEDIUM-01");
  for (const [query, severityLevel] of [
    ["중요도 상", "high"],
    ["고위험", "high"],
    ["중요도 중", "medium"],
    ["중요도 하", "low"],
  ]) {
    assert.ok(rankRecords(records, query).length > 0, query);
    assert.ok(
      rankRecords(records, query).every(
        ({record}) => record.severityLevel === severityLevel,
      ),
      query,
    );
  }
});

test("uses article section semantics for ranking and result context", () => {
  const structuredRecords = [
    createRecord({
      code: "ACTION-01",
      title: "관리자 계정 관리",
      domainIdentifier: "unix",
      domainLabel: "Unix 서버",
      categoryLabel: "계정 관리",
      searchableText: "관리자 계정 정책",
      searchSections: {
        action: "root 원격 접속을 차단하도록 설정",
      },
      order: 1,
    }),
    createRecord({
      code: "INSPECTION-01",
      title: "관리자 계정 관리 확인",
      domainIdentifier: "unix",
      domainLabel: "Unix 서버",
      categoryLabel: "계정 관리",
      searchableText: "root 원격 접속 차단 설정 여부 점검",
      order: 2,
    }),
  ];

  const [topResult] = rankRecords(
    structuredRecords,
    "root 원격 접속을 어떻게 차단해",
  );
  assert.equal(topResult.record.code, "ACTION-01");
  assert.equal(topResult.contextSection, "action");
  assert.equal(topResult.contextLabel, "조치 방법");
});

test("returns no result for request words without a security intent", () => {
  assert.deepEqual(rankRecords(records, "항목을 찾아줘"), []);
});
