(() => {
  const koreanParticles = [
    "에게서",
    "에서부터",
    "으로부터",
    "까지",
    "부터",
    "처럼",
    "보다",
    "에게",
    "에서",
    "으로",
    "이나",
    "라도",
    "과",
    "와",
    "을",
    "를",
    "이",
    "가",
    "은",
    "는",
    "의",
    "도",
    "만",
    "에",
    "로",
  ];
  const koreanRequestSuffixes = [
    "해주세요",
    "하려고",
    "하려는",
    "하는",
    "해야",
    "하고",
    "되는",
    "되어",
    "해줘",
    "싶어",
    "싶은",
    "인가요",
    "일까요",
    "나요",
    "줘",
    "된",
  ];
  const naturalLanguageStopWords = new Set([
    "것",
    "관련",
    "대한",
    "대해",
    "방법",
    "문서",
    "보여",
    "알려",
    "어떤",
    "어떻게",
    "중요",
    "중요도",
    "위험",
    "여부",
    "점검",
    "점검항목",
    "조치",
    "찾아",
    "항목",
    "싶어",
    "싶은",
    "두면",
    "시",
    "하",
    "되",
  ]);
  const synonymGroups = [
    ["비밀번호", "패스워드", "password", "암호"],
    ["계정", "사용자계정", "사용자", "아이디", "id", "account"],
    ["로그", "로깅", "기록", "감사기록", "audit", "logging"],
    ["원격", "remote", "ssh", "telnet"],
    ["접속", "접근", "로그인", "login", "access"],
    [
      "차단",
      "제한",
      "금지",
      "비활성화",
      "막",
      "막기",
      "막아",
      "막고",
      "막는",
      "숨기",
      "숨기기",
      "숨겨",
    ],
    ["보관", "보존", "유지", "저장"],
    ["기간", "주기", "시간", "오래", "장기", "duration", "period"],
    ["백업", "복구", "스냅샷", "backup", "restore"],
    ["암호화", "암호화통신", "tls", "ssl", "https", "encryption"],
    ["취약점", "취약", "보안취약", "vulnerability"],
    ["삭제", "제거", "정리", "불필요"],
    ["잠금", "락", "lock", "실패횟수", "임계값"],
    ["권한", "접근권한", "permission", "privilege"],
    ["업로드", "파일업로드", "upload"],
    ["다운로드", "파일다운로드", "download"],
    ["디렉터리", "폴더", "directory"],
    ["목록", "리스팅", "인덱싱", "listing", "indexing"],
    ["크기", "용량", "사이즈", "size"],
    ["변경", "수정", "내역", "이력", "history"],
    ["세션", "session"],
    ["셸", "쉘", "shell"],
    ["타임아웃", "종료시간", "자동종료", "timeout"],
    ["기술지원종료", "지원종료", "eol"],
    ["스택트레이스", "스택", "stacktrace", "stack"],
    ["sql인젝션", "sql 인젝션", "sql삽입", "sql주입", "sqlinjection"],
  ];
  const domainIntentDefinitions = [
    {identifiers: ["unix"], aliases: ["유닉스", "unix"]},
    {identifiers: ["windows"], aliases: ["윈도우", "windows", "window server"]},
    {identifiers: ["web-service"], aliases: ["웹 서비스", "웹 서버", "web service", "web server"]},
    {identifiers: ["security-device"], aliases: ["보안 장비", "방화벽", "ids", "ips"]},
    {
      identifiers: ["network-device"],
      aliases: ["네트워크 장비", "라우터", "스위치", "router", "switch"],
    },
    {identifiers: ["control-system"], aliases: ["제어시스템", "제어 시스템", "ics"]},
    {identifiers: ["pc"], aliases: ["pc", "피시", "개인용 컴퓨터"]},
    {
      identifiers: ["dbms"],
      aliases: ["dbms", "데이터베이스", "database", "오라클", "oracle", "mysql", "mssql"],
    },
    {identifiers: ["mobile-communication"], aliases: ["이동통신", "모바일 통신"]},
    {
      identifiers: ["web-application"],
      aliases: ["웹 애플리케이션", "웹 어플리케이션", "웹앱", "web application"],
    },
    {
      identifiers: ["virtualization-device"],
      aliases: ["가상화 장비", "가상머신", "가상 머신", "vmware", "esxi", "hypervisor"],
    },
    {identifiers: ["cloud"], aliases: ["클라우드", "cloud", "aws", "azure", "gcp"]},
  ];
  const targetIntentDefinitions = [
    {identifiers: ["linux"], aliases: ["리눅스", "linux", "ubuntu", "우분투", "centos", "redhat", "debian"]},
    {identifiers: ["solaris"], aliases: ["solaris", "솔라리스"]},
    {identifiers: ["aix"], aliases: ["aix"]},
    {identifiers: ["hp-ux"], aliases: ["hp-ux", "hpux"]},
  ];
  const severityIntentDefinitions = [
    {identifiers: ["high"], aliases: ["중요도 상", "상 위험", "고위험", "high"]},
    {identifiers: ["medium"], aliases: ["중요도 중", "중 위험", "medium"]},
    {identifiers: ["low"], aliases: ["중요도 하", "하 위험", "저위험", "low"]},
  ];
  const sectionIntentDefinitions = [
    {identifiers: ["purpose"], aliases: ["왜", "이유", "목적"]},
    {identifiers: ["threat"], aliases: ["위협", "위험", "문제", "공격"]},
    {identifiers: ["judgment"], aliases: ["기준", "판단", "양호", "취약"]},
    {identifiers: ["action"], aliases: ["어떻게", "방법", "해결", "설정"]},
    {identifiers: ["impact"], aliases: ["영향", "부작용", "장애"]},
  ];
  const searchSectionDefinitions = [
    {identifier: "inspection", label: "점검 내용", score: 165},
    {identifier: "purpose", label: "점검 목적", score: 145},
    {identifier: "threat", label: "보안 위협", score: 140},
    {identifier: "judgment", label: "판단 기준", score: 155},
    {identifier: "action", label: "조치 방법", score: 135},
    {identifier: "impact", label: "조치 영향", score: 90},
    {identifier: "guidance", label: "조치 사례", score: 110},
    {identifier: "reference", label: "참고", score: 60},
  ];
  const normalizedRecordCache = new WeakMap();

  const normalizeText = (value) =>
    String(value || "").normalize("NFC").toLocaleLowerCase("ko");

  const intentOnlyTokens = new Set(
    [
      ...domainIntentDefinitions,
      ...targetIntentDefinitions,
      ...severityIntentDefinitions,
      ...sectionIntentDefinitions,
    ].flatMap((definition) =>
      definition.aliases
        .map(normalizeText)
        .filter((alias) => !alias.includes(" ")),
    ),
  );

  const normalizeCode = (value) =>
    normalizeText(value).replaceAll("-", "").replaceAll("_", "").replaceAll(" ", "");

  const tokenizeText = (value) =>
    normalizeText(value)
      .replace(/[^\p{L}\p{N}._/#=:+-]+/gu, " ")
      .trim()
      .split(/\s+/)
      .filter(Boolean);

  const stripKoreanSuffix = (value, suffixes) => {
    if (!/^[가-힣]+$/u.test(value)) {
      return value;
    }
    for (const suffix of suffixes) {
      if (value.endsWith(suffix) && value.length > suffix.length) {
        return value.slice(0, -suffix.length);
      }
    }
    return value;
  };

  const normalizeNaturalToken = (value) => {
    const withoutParticle = stripKoreanSuffix(value, koreanParticles);
    return stripKoreanSuffix(withoutParticle, koreanRequestSuffixes);
  };

  const synonymGroupForToken = (token) =>
    synonymGroups.find((group) => group.includes(token));

  const buildQueryUnits = (meaningfulTokens) => {
    const units = [];
    const unitKeys = new Set();
    for (let index = 0; index < meaningfulTokens.length; index += 1) {
      let label = meaningfulTokens[index];
      let alternatives = synonymGroupForToken(label);
      for (const phraseLength of [3, 2]) {
        const phraseTokens = meaningfulTokens.slice(index, index + phraseLength);
        if (phraseTokens.length !== phraseLength) {
          continue;
        }
        const phraseAlternatives = synonymGroupForToken(phraseTokens.join(""));
        if (!phraseAlternatives) {
          continue;
        }
        label = phraseTokens.join(" ");
        alternatives = phraseAlternatives;
        index += phraseLength - 1;
        break;
      }
      alternatives ||= [label];
      const key = [...alternatives].sort().join("\u0000");
      if (unitKeys.has(key)) {
        continue;
      }
      unitKeys.add(key);
      units.push({label, alternatives});
    }
    return units;
  };

  const containsAlias = (normalizedQuery, queryTokens, alias) => {
    const normalizedAlias = normalizeText(alias);
    return normalizedAlias.includes(" ")
      ? normalizedQuery.includes(normalizedAlias)
      : queryTokens.includes(normalizedAlias);
  };

  const resolveIntents = (normalizedQuery, queryTokens, definitions) =>
    definitions
      .filter((definition) =>
        definition.aliases.some((alias) =>
          containsAlias(normalizedQuery, queryTokens, alias),
        ),
      )
      .flatMap((definition) => definition.identifiers);

  const resolveIntentPhraseTokens = (normalizedQuery) =>
    new Set(
      [
        ...domainIntentDefinitions,
        ...targetIntentDefinitions,
        ...severityIntentDefinitions,
        ...sectionIntentDefinitions,
      ].flatMap((definition) =>
        definition.aliases
          .map(normalizeText)
          .filter(
            (alias) => alias.includes(" ") && normalizedQuery.includes(alias),
          )
          .flatMap((alias) => tokenizeText(alias).map(normalizeNaturalToken)),
      ),
    );

  const analyzeQuery = (query) => {
    const normalizedQuery = normalizeText(query).replace(/\s+/g, " ").trim();
    const queryTokens = tokenizeText(normalizedQuery);
    const meaningfulTokens = queryTokens
      .map(normalizeNaturalToken)
      .filter((token) => token && !naturalLanguageStopWords.has(token));
    const intentTokens = [...new Set([...queryTokens, ...meaningfulTokens])];
    const contextTokens = [
      ...new Set(
        queryTokens
          .map(normalizeNaturalToken)
          .filter((token) => token && !naturalLanguageStopWords.has(token)),
      ),
    ];
    const intentPhraseTokens = resolveIntentPhraseTokens(normalizedQuery);
    const units = buildQueryUnits(
      meaningfulTokens.filter(
        (token) =>
          !intentOnlyTokens.has(token) && !intentPhraseTokens.has(token),
      ),
    );
    return {
      originalQuery: String(query || "").trim(),
      normalizedQuery,
      normalizedCode: normalizeCode(query),
      normalizedTopicPhrase: units.map(({label}) => label).join(" "),
      units,
      contextTokens,
      domainIntents: resolveIntents(
        normalizedQuery,
        intentTokens,
        domainIntentDefinitions,
      ),
      targetIntents: resolveIntents(
        normalizedQuery,
        intentTokens,
        targetIntentDefinitions,
      ),
      severityIntents: resolveIntents(
        normalizedQuery,
        intentTokens,
        severityIntentDefinitions,
      ),
      sectionIntents: resolveIntents(
        normalizedQuery,
        intentTokens,
        sectionIntentDefinitions,
      ),
    };
  };

  const normalizeRecord = (record) => {
    const cachedRecord = normalizedRecordCache.get(record);
    if (cachedRecord) {
      return cachedRecord;
    }
    const sourceSections = record.searchSections || {
      inspection: record.searchableText || "",
    };
    const sections = Object.fromEntries(
      searchSectionDefinitions.map(({identifier}) => [
        identifier,
        normalizeText(sourceSections[identifier]),
      ]),
    );
    const normalizedRecord = {
      title: normalizeText(record.title),
      classification: normalizeText(
        [
          record.domainLabel,
          record.categoryLabel,
          ...record.targetLabels,
          record.sourceTargetText,
        ].join(" "),
      ),
      sections,
      allText: Object.values(sections).join(" "),
      exactTerms: record.exactTerms.map(normalizeText),
    };
    normalizedRecordCache.set(record, normalizedRecord);
    return normalizedRecord;
  };

  const matchUnit = (unit, normalizedRecord, sectionIntents) => {
    let score = 0;
    let matchedAlternative = null;
    let matchedField = null;
    for (const alternativeValue of unit.alternatives) {
      const alternative = normalizeText(alternativeValue);
      if (!alternative) {
        continue;
      }
      let alternativeScore = 0;
      let alternativeField = null;
      const considerMatch = (candidateScore, candidateField) => {
        if (candidateScore > alternativeScore) {
          alternativeScore = candidateScore;
          alternativeField = candidateField;
        }
      };
      if (normalizedRecord.title === alternative) {
        considerMatch(260, "title");
      } else if (normalizedRecord.title.includes(alternative)) {
        considerMatch(190, "title");
      }
      if (normalizedRecord.exactTerms.some((term) => term === alternative)) {
        considerMatch(125, "exactTerm");
      } else if (normalizedRecord.exactTerms.some((term) => term.includes(alternative))) {
        considerMatch(95, "exactTerm");
      }
      if (normalizedRecord.classification.includes(alternative)) {
        considerMatch(105, "classification");
      }
      for (const section of searchSectionDefinitions) {
        if (!normalizedRecord.sections[section.identifier].includes(alternative)) {
          continue;
        }
        const sectionMatchesIntent =
          sectionIntents.includes(section.identifier) ||
          (section.identifier === "guidance" && sectionIntents.includes("action"));
        const intentBoost = sectionMatchesIntent ? 90 : 0;
        considerMatch(
          section.score + intentBoost,
          "section:" + section.identifier,
        );
      }
      if (alternativeScore > score) {
        score = alternativeScore;
        matchedAlternative = alternative;
        matchedField = alternativeField;
      }
    }
    return {score, matchedAlternative, matchedField};
  };

  const scoreRecord = (record, queryAnalysis) => {
    if (!queryAnalysis.originalQuery) {
      return {
        score: 1,
        matchedLabels: [],
        matchedAlternatives: [],
        matchedFields: [],
        coverage: 1,
      };
    }
    if (normalizeCode(record.code) === queryAnalysis.normalizedCode) {
      return {
        score: 5000,
        matchedLabels: [record.code],
        matchedAlternatives: [normalizeText(record.code)],
        matchedFields: ["code"],
        coverage: 1,
      };
    }

    const normalizedRecord = normalizeRecord(record);
    const exactTermMatch = normalizedRecord.exactTerms.find(
      (term) => term === queryAnalysis.normalizedQuery,
    );
    if (exactTermMatch) {
      return {
        score: 4000,
        matchedLabels: [queryAnalysis.originalQuery],
        matchedAlternatives: [exactTermMatch],
        matchedFields: ["exactTerm"],
        coverage: 1,
      };
    }
    let score = 0;
    const matchedLabels = [];
    const matchedAlternatives = [];
    const matchedFields = [];
    for (const unit of queryAnalysis.units) {
      const match = matchUnit(
        unit,
        normalizedRecord,
        queryAnalysis.sectionIntents,
      );
      if (match.score === 0) {
        continue;
      }
      const specificityMultiplier = unit.label.includes(" ")
        ? match.matchedField === "title"
          ? 3
          : 1.25
        : 1;
      score += match.score * specificityMultiplier;
      matchedLabels.push(unit.label);
      matchedAlternatives.push(match.matchedAlternative);
      matchedFields.push(match.matchedField);
    }

    if (queryAnalysis.units.length > 0) {
      if (normalizedRecord.title === queryAnalysis.normalizedQuery) {
        score += 900;
      } else if (normalizedRecord.title.startsWith(queryAnalysis.normalizedQuery)) {
        score += 500;
      } else if (normalizedRecord.title.includes(queryAnalysis.normalizedQuery)) {
        score += 320;
      } else if (normalizedRecord.allText.includes(queryAnalysis.normalizedQuery)) {
        score += 120;
      }
      if (
        queryAnalysis.normalizedTopicPhrase !== queryAnalysis.normalizedQuery &&
        normalizedRecord.title.includes(queryAnalysis.normalizedTopicPhrase)
      ) {
        score += 450;
      }
    }

    if (queryAnalysis.domainIntents.includes(record.domainIdentifier)) {
      score += 260;
    }
    if (
      queryAnalysis.targetIntents.some((identifier) =>
        record.targetIdentifiers.some((targetIdentifier) =>
          targetIdentifier === identifier ||
          (identifier === "linux" && /^(?:rhel|ubuntu|debian)-\d/.test(targetIdentifier)),
        ),
      )
    ) {
      score += 170;
    }
    if (queryAnalysis.severityIntents.includes(record.severityLevel)) {
      score += 120;
    }

    const unitCount = queryAnalysis.units.length;
    const matchedUnitCount = matchedLabels.length;
    const minimumMatchedUnitCount = unitCount === 0
      ? 0
      : unitCount <= 2
        ? 1
        : Math.ceil(unitCount / 2);
    if (matchedUnitCount < minimumMatchedUnitCount) {
      return {
        score: 0,
        matchedLabels: [],
        matchedAlternatives: [],
        matchedFields: [],
        coverage: 0,
      };
    }
    const coverage = unitCount ? matchedUnitCount / unitCount : 1;
    score = Math.round(score * (0.55 + coverage * 0.45));
    return {score, matchedLabels, matchedAlternatives, matchedFields, coverage};
  };

  const selectRelevantContext = (record, ranking, queryAnalysis) => {
    const sourceSections = record.searchSections || {
      inspection: record.searchableText || "",
    };
    const contextAlternatives = [
      ...new Set([
        ...queryAnalysis.contextTokens,
        ...ranking.matchedAlternatives,
      ]),
    ];
    const exactTermAlternative = ranking.matchedFields.includes("exactTerm")
      ? ranking.matchedAlternatives[0]
      : null;
    const exactTermHasContext =
      exactTermAlternative &&
      Object.values(sourceSections).some((text) =>
        normalizeText(text).includes(exactTermAlternative),
      );
    if (exactTermAlternative && !exactTermHasContext) {
      return {
        context: ranking.matchedLabels[0],
        contextLabel: "설정값",
        contextSection: "technicalTerm",
      };
    }
    const candidates = searchSectionDefinitions
      .map((section, order) => {
        const text = String(sourceSections[section.identifier] || "")
          .replace(/\s+/g, " ")
          .trim();
        const normalizedText = normalizeText(text);
        const positions = contextAlternatives
          .filter(Boolean)
          .map((alternative) => normalizedText.indexOf(alternative))
          .filter((position) => position >= 0);
        const sectionMatchesIntent =
          queryAnalysis.sectionIntents.includes(section.identifier) ||
          (section.identifier === "guidance" &&
            queryAnalysis.sectionIntents.includes("action"));
        return {
          ...section,
          order,
          text,
          positions,
          contextScore:
            positions.length * 100 +
            (sectionMatchesIntent ? 500 : 0) +
            section.score,
        };
      })
      .filter(({text}) => text)
      .sort(
        (left, right) =>
          right.contextScore - left.contextScore || left.order - right.order,
      );
    const selected = candidates[0] || {
      identifier: "inspection",
      label: "점검 내용",
      text: record.title,
      positions: [],
    };
    const firstPosition = selected.positions.length
      ? selected.positions[0]
      : 0;
    const start = Math.max(0, firstPosition - 60);
    const end = Math.min(selected.text.length, start + 220);
    return {
      context:
        (start > 0 ? "…" : "") +
        selected.text.slice(start, end) +
        (end < selected.text.length ? "…" : ""),
      contextLabel: selected.label,
      contextSection: selected.identifier,
    };
  };

  const addResultContexts = (rankedRecords, query) => {
    const queryAnalysis = analyzeQuery(query);
    return rankedRecords.map((ranking) => ({
      ...ranking,
      ...selectRelevantContext(ranking.record, ranking, queryAnalysis),
    }));
  };

  const rankRecords = (records, query, {includeContext = true} = {}) => {
    const queryAnalysis = analyzeQuery(query);
    const rankedRecords = records
      .map((record) => {
        const ranking = scoreRecord(record, queryAnalysis);
        return {
          record,
          ...ranking,
        };
      })
      .filter(({score}) => !queryAnalysis.originalQuery || score > 0)
      .sort(
        (left, right) =>
          right.score - left.score ||
          right.coverage - left.coverage ||
          left.record.order - right.record.order,
      );
    return includeContext
      ? addResultContexts(rankedRecords, query)
      : rankedRecords;
  };

  globalThis.KisaNaturalSearch = Object.freeze({
    addResultContexts,
    analyzeQuery,
    normalizeCode,
    normalizeText,
    rankRecords,
    scoreRecord,
  });
})();
