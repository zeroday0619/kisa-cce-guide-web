# UNIX 개정판 운영

개정판은 KISA 원문 UNIX U-01~U-67을 최신 Linux 운영 환경에 맞춰 작성한 독립적인 최종 편집본이다. 원문과 같은 문서 구성, 문장 형식과 화면 구성으로 제공한다. 공식 KISA 개정본은 아니며 원문이 다루는 Solaris, AIX, HP-UX의 대체 지침은 제공하지 않는다.

## 대상 배포판

| 플랫폼 식별자 | 대상 | 공식 자료 |
| --- | --- | --- |
| `rhel-10` | RHEL 10 계열 | [RHEL 릴리스 이력](https://access.redhat.com/articles/red-hat-enterprise-linux-release-dates) |
| `ubuntu-26.04` | Ubuntu 26.04 LTS | [LTS 변경 사항](https://documentation.ubuntu.com/release-notes/26.04/summary-for-lts-users/) |
| `debian-13` | Debian 13 trixie | [Debian 13 안내](https://www.debian.org/releases/trixie/index) |

문서 조사 기준일은 2026-09-14다. 마이너 업데이트, 신규 설치와 업그레이드 설치, 최소 이미지와 일반 서버 설치는 패키지·서비스 구성이 다를 수 있다. 실제 점검 전 `/etc/os-release`, 설치 패키지와 활성 서비스를 확인한다. RHEL 호환 배포판에 대한 실행 검증을 RHEL 검증으로 간주하지 않는다.

## 원본과 개정판 분리

원본 `content/criteria/`와 provenance, source PDF, registry는 기존 계약을 유지한다. 개정 콘텐츠는 `content/revisions/unix/`에만 작성한다. 원본 382개 항목의 검색·정규화 데이터에 개정 본문을 섞지 않는다.

각 개정 Markdown은 다음 metadata를 가진다.

```yaml
criterionCode: U-01
title: root 계정 원격 접속 제한
platforms: [rhel-10, ubuntu-26.04, debian-13]
status: final
sources:
  - title: Ubuntu OpenSSH server
    url: https://ubuntu.com/server/docs/how-to/security/openssh-server/
```

Metadata는 YAML frontmatter에 기록한다. 본문은 원본과 같은 구성과 명사형 점검·조치 문장으로 작성한다.

1. `개요`: 점검 내용, 점검 목적, 보안 위협과 필요한 참고 설명
2. `점검 대상 및 판단 기준`: 대상, 양호·취약 판단 기준, 조치 방법, 조치 시 영향
3. `점검 및 조치 사례`: RHEL 10, Ubuntu 26.04 LTS, Debian 13별 번호가 있는 절차와 명령·설정 예시

문서에 포함된 명령어는 변환·빌드 과정에서 실행하지 않으며 fenced code block에 언어와 용도를 지정한다. 공식 문헌은 해당 변경의 근거가 되는 페이지를 연결한다. 화면은 원본의 공통 항목·목록·검색 템플릿과 블록 renderer를 사용한다.

## 분할 설정 작성 기준

서비스가 지원하는 include 또는 drop-in 경로에 역할별 설정 파일을 작성한다. 본문에는 포함 지시문, 파일 이름 규칙, 읽기 순서와 중복 설정 처리 방식을 함께 설명한다. `.d/` 디렉터리의 존재만으로 설정 적용을 판단하지 않으며 기본 설정 파일, 패키지 제공 설정과 실행 인수도 점검한다.

조치 사례는 서비스가 제공하는 문법 검사와 최종 적용 설정 확인을 포함한다. 분할 설정을 지원하지 않는 파일은 해당 프로그램의 설정 방식을 유지한다. 패키지가 생성하는 파일은 배포판이 제공하는 관리 도구나 사용자 정의 설정 경로로 관리한다.

## 검색

원본 검색과 UNIX 개정판 검색은 검색 화면의 판본 링크로 이동한다. 개정판 검색은 `/revised/dataset/search-index.json`의 67개 항목을 대상으로 하며 결과는 개정 문서로 연결된다. 항목 코드, 설정 키, 본문과 Linux 계열 배포판 이름으로 검색하고 분류·중요도·대상 필터를 적용한다.

## 검증

```bash
uv run python -m conversion.validate_content
uv run python -m conversion.build_content
uv run pytest tests/test_revised_content.py tests/test_site_generation.py tests/test_github_pages_workflow.py -q
node --test tests/search-core.test.cjs tests/theme.test.cjs
```

Canonical 검증은 원본 계층을 검사한다. 빌드는 별도로 개정판 67개 파일의 코드, 플랫폼, `final` 상태, 참고 문헌과 원본 형식의 Markdown 구조를 검증한다. URL 형식 검사는 외부 문헌의 접근 가능성이나 주장 정확성을 보증하지 않는다.

`final`은 편집 완료 상태다. 자동 테스트는 문서 구조, 판본 분리, 화면 구성, 링크와 결정성을 검사한다. 각 항목의 조치 시 영향과 배포판별 절차에서 운영 적용에 필요한 조건을 설명한다.

## Dataset 버전 2 전환

개정 dataset의 `schemaVersion`은 2다. 버전 1의 `status: draft`를 버전 2의 `status: final`로 변경하고 본문을 원본 문서 구성으로 재작성했다. 기존 개정 콘텐츠 67개와 schema, builder, 테스트를 함께 전환한다. 제목이나 상태 문자열만 바꾸어 버전 1 데이터를 재사용하지 않는다. 원본 dataset의 schema와 382개 검색 record는 변경하지 않는다.

## 두 판본 동시 배포

최종 편집본 후속 작업은 `feat/unix-final-edition` 브랜치에서 분리한다. Pages 수동 workflow는 선택한 브랜치의 원본과 개정판을 한 artifact로 만든다. `--base-path`는 두 판본에 동일하게 적용된다.

| 산출물 | URL |
| --- | --- |
| 원본 전체 | `/` |
| 원본 UNIX 항목 | `/unix/u-01/` |
| 개정판 목록 | `/revised/` |
| 개정 UNIX 분야 목록 | `/revised/unix/` |
| 개정판 검색 | `/revised/search/` |
| 개정 UNIX 항목 | `/revised/unix/u-01/` |
| 개정판 데이터 | `/revised/dataset.json` |
| 개정 항목 데이터 | `/revised/dataset/criteria/unix/u-01.json` |

예를 들어 프로젝트 base path가 `/kisa-cce-guide-web`이면 개정판 목록 URL은 `/kisa-cce-guide-web/revised/`다. 별도 배포를 순서대로 실행하면 같은 Pages 사이트를 덮어쓰므로 한 번의 workflow에서 두 판본을 함께 업로드한다. Workflow 수정만으로 원격 배포가 실행되지는 않는다.
