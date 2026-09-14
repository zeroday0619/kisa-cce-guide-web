# UNIX 개정판 운영

개정판은 KISA 원문 UNIX U-01~U-67을 최신 Linux 운영 환경에 맞춰 설명한 비공식 편집 초안이다. 원문이 다루는 Solaris, AIX, HP-UX의 대체 지침은 제공하지 않는다. 원문 판정 기준과 최신 배포판의 구현 차이를 구분하고 실제 환경의 적용 여부를 확인한다.

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
status: draft
sources:
  - title: Ubuntu OpenSSH server
    url: https://ubuntu.com/server/docs/how-to/security/openssh-server/
```

Metadata는 YAML frontmatter에 기록한다. 본문에는 항목별 적용 범위, 점검, 조치와 운영 영향을 설명한다. 명령어는 실행 가능한 프로그램 입력으로 사용하지 않으며 fenced code block에 언어를 지정한다. 공식 문헌은 해당 변경의 근거가 되는 페이지를 연결한다.

## 검증

```bash
uv run python -m conversion.validate_content
uv run python -m conversion.build_content
uv run pytest tests/test_revised_content.py tests/test_site_generation.py tests/test_github_pages_workflow.py -q
node --test tests/search-core.test.cjs tests/theme.test.cjs
```

Canonical 검증은 원본 계층을 검사한다. 빌드는 별도로 개정판 67개 파일의 코드, 플랫폼, 초안 상태, 참고 문헌과 Markdown 기본 구조를 검증한다. URL 형식 검사는 외부 문헌의 접근 가능성이나 주장 정확성을 보증하지 않는다.

현재 개정판의 상태는 `draft`다. 자동 테스트는 문서 구조, 판본 분리, 링크와 결정성을 검사한다. 실제 RHEL·Ubuntu·Debian VM의 PAM 인증, SSH 재접속, 서비스 재시작, 패키지 기본값과 장애 복구는 **Verification required**다. 실제 운영 적용 전 해당 환경에서 별도로 검증한다. 사람 검토가 완료된 것으로 표시하지 않는다.

## 두 판본 동시 배포

`feat/unix-modern-linux-edition` 브랜치에서 개정 작업을 분리한다. Pages 수동 workflow는 선택한 브랜치의 원본과 개정판을 한 artifact로 만든다. `--base-path`는 두 판본에 동일하게 적용된다.

| 산출물 | URL |
| --- | --- |
| 원본 전체 | `/` |
| 원본 UNIX 항목 | `/unix/u-01/` |
| 개정판 목록 | `/revised/` |
| 개정 UNIX 항목 | `/revised/unix/u-01/` |
| 개정판 데이터 | `/revised/dataset.json` |

예를 들어 프로젝트 base path가 `/kisa-cce-guide-web`이면 개정판 목록 URL은 `/kisa-cce-guide-web/revised/`다. 별도 배포를 순서대로 실행하면 같은 Pages 사이트를 덮어쓰므로 한 번의 workflow에서 두 판본을 함께 업로드한다. Workflow 수정만으로 원격 배포가 실행되지는 않는다.
