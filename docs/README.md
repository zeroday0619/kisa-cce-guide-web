# 프로젝트 문서

이 디렉터리는 KISA CCE 가이드 변환 저장소의 설계 결정, 운영 절차, 디자인 참고 자료를 분리해 관리한다. 콘텐츠 규범과 승인 조건은 루트의 [문서 변환 정책](../CONVERSION_POLICY.md)이 기준이다.

## 문서 지도

| 문서 | 내용 |
| --- | --- |
| [Codex-native 변환 아키텍처](architecture/codex-native-conversion.md) | 변환 소유권, 데이터 흐름, 격리, 재개 조건, 성능 기준 |
| [변환 워크플로](operations/conversion-workflows.md) | 초기 corpus 생성, Codex-native 변환, 재개와 산출물 |
| [Legacy 변환 워크플로](operations/legacy-conversion.md) | Structured-JSON 기반 migration 경로와 병렬 실행 |
| [빌드와 릴리스](operations/build-and-release.md) | 검증, 빌드, 로컬 서버, Pages artifact, 릴리스 게이트 |
| [UNIX 개정판 운영](operations/unix-revised-edition.md) | Linux 배포판 기준, 원본 분리, 개정 검토와 동시 배포 |
| [NVIDIA 디자인 참고 분석](design/nvidia-reference-analysis.md) | 현재 사이트 디자인의 출발점이 된 외부 레퍼런스 분석 |

## 문서 경계

- `README.md`는 저장소 개요, 확인된 현재 상태, 빠른 시작만 제공한다.
- `CONVERSION_POLICY.md`는 규범 용어, 콘텐츠 계약, provenance, 검토와 릴리스 조건을 정의한다.
- `docs/architecture/`는 설계 결정과 안전 경계를 설명한다.
- `docs/operations/`는 실행 명령, 생성 경로, 실패와 재개 절차를 설명한다.
- `docs/design/`는 디자인 시스템과 참고 분석을 보존한다.
- `conversion/prompts/`는 실행 시 checksum에 포함되는 모델 계약이다. 일반 프로젝트 문서가 아니다.
- `site/skill/`은 빌드된 사이트에 배포되는 LLM 탐색 지침의 원본이다. 일반 프로젝트 문서가 아니다.

문서에서 저장소 경로를 변경할 때는 코드의 경로 상수, 생성 task의 경로 필드, 테스트 fixture와 함께 검증한다.
