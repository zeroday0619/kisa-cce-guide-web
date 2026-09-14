---
name: kisa-cce-guide-explorer
description: Finds and reads published KISA CCE 2026 criteria and the independent UNIX Linux revision. Use for judgment conditions, remediation procedures, affected targets, and security settings, selecting the edition for the requested platform.
---

# KISA CCE Guide Explorer

Use this skill to navigate the published KISA CCE guide website and answer from the rendered criterion pages.

## When to Use

Use this skill when a request involves any of the following:

- KISA CCE criterion codes or titles
- infrastructure vulnerability checks
- compliant and vulnerable judgment conditions
- remediation methods or operational impact
- Unix, Windows, network device, DBMS, cloud, web, or other supported domains
- exact commands, paths, configuration values, products, or protocols shown in the guide

## Core Workflow

1. Select the edition using the user's request and target platform as described below.
2. Start with that edition's search when the user gives a question, keyword, setting, or criterion code.
3. Open the best matching criterion page. Open several candidates when the request is ambiguous.
4. Read the visible page header, edition, judgment criteria, remediation summary, impact, and relevant platform-specific procedure.
5. Name the edition used and answer with links to the criterion page and the nearest section anchors.

## Edition Selection

- Follow an explicitly requested original or revised edition.
- For RHEL 10, Ubuntu 26.04 LTS, or Debian 13 questions about UNIX U-01 through U-67, prefer the independent Linux revision and its `/revised/search/` directory.
- For the original KISA wording, Solaris, AIX, HP-UX, or non-UNIX domains, use the original edition and `/search/`.
- A UNIX code alone does not identify a platform or edition. If neither is specified, identify both editions and clarify the target before choosing platform-specific procedures. Do not silently substitute revised requirements for the original.
- When comparing editions, open both articles using the visible counterpart link. Keep each edition's judgment conditions and procedures separate.

The original edition contains 382 criteria across 12 domains. The revision contains 67 UNIX criteria for the three Linux targets above. It is an independent editorial revision, not an official KISA revision, and does not replace Solaris, AIX, or HP-UX guidance. Editorial status `final` does not establish execution testing or human approval. The original KISA PDF remains the authority for the source guide.

## HTTP Usage

Use the guide base URL supplied by the user. If this file came from the guide website, remove `/SKILL.md` from its URL to obtain the base URL. Preserve any deployment subpath, such as `/kisa-cce-guide-web`. Append the paths below to that base rather than resolving them against the host root. Do not guess a missing deployment URL.

Append these paths to the base URL:

- Home: `/`
- LLM entrypoint directory: `/llms.txt`
- Search: `/search/?q={url-encoded-query}`
- Technical domain: `/{domainIdentifier}/`
- Category: `/{domainIdentifier}/{categoryIdentifier}/`
- Criterion article: `/{domainIdentifier}/{slug}/`
- Independent Linux revision directory: `/revised/`
- Revised UNIX domain: `/revised/unix/`
- Revised category: `/revised/unix/{categoryIdentifier}/`
- Independent revised UNIX article: `/revised/unix/{slug}/`
- Revised edition search: `/revised/search/?q={url-encoded-query}`

Examples:

- `/search/?q=PermitRootLogin`
- `/unix/`
- `/unix/unix-account-management/`
- `/unix/u-01/`
- `/revised/search/?q=PermitRootLogin`
- `/revised/unix/u-01/`
- `/web-application/si/`

Do not inspect repository source files, build artifacts, or JSON datasets unless the user explicitly asks for implementation details or machine-readable data.

## Page Types

### Search Directory

Use the selected edition's search for natural-language questions, codes, titles, commands, paths, settings, products, and protocols. Search order identifies candidates; it does not prove that the first result is the only applicable criterion.

Search at `/search/` covers the original 382 criteria. Search at `/revised/search/` covers the 67 revised UNIX criteria for RHEL 10, Ubuntu 26.04 LTS, and Debian 13. Both editions use the same article structure and navigation. Cite the edition used and follow its counterpart link when comparing requirements.

### Domain and Category Pages

Use domain and category pages when the environment or technology is already known. These pages narrow the visible criterion links without requiring knowledge of internal identifiers in advance.

### Criterion Articles

Treat the rendered criterion article as the answer source. Read these visible sections as needed:

- `점검 내용`: what is inspected
- `점검 목적`: why it is inspected
- `보안 위협`: threats and consequences
- `대상`: applicable environments or products
- `판단 기준`: compliant and vulnerable conditions
- `조치 방법`: remediation summary
- `조치 시 영향`: documented operational impact
- `점검 및 조치 사례`: platform- or product-specific procedures and examples
- `참고`: definitions and supporting terminology

## Evidence Rules

- Base claims on content visible on the published criterion page.
- Preserve commands, paths, settings, and values exactly as displayed.
- Keep each command or output with the procedure step and platform heading that contains it.
- Do not combine platform- or vendor-specific procedures unless the page explicitly applies the same instruction to them.
- Treat target wording containing `등` as non-exhaustive.
- Do not infer support for an unlisted version, operating system, or product.
- Keep the answer within the platform and configuration covered by the cited article.
- Do not execute remediation unless the user explicitly requests it.

## Best Practices

- Prefer an exact criterion code match when one is available.
- Verify search snippets against the full criterion page.
- Open more than one article when scopes overlap.
- Cite the full article instead of the search result page.
- Link specific claims to the nearest visible section anchor.
- Separate facts stated on the page from operational recommendations or inference.

## Troubleshooting

### Search results do not update

Interactive ranking may be unavailable in a text-only web client. Use the complete criterion directory already rendered on the selected edition's `/search/` or `/revised/search/`, or navigate through that edition's domain page.

### A page is too broad

Move from the domain page to a category, then open a specific criterion article. Prefer a criterion page over a broad listing when answering.

### A setting appears under several platforms

Read the surrounding platform heading and procedure step. Report only the variant that matches the user's environment, or present each variant separately when the environment is unknown.
