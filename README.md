# AgentCore Registry 활용하기

AWS Agent Registry(AgentCore Registry)는 조직 내 AI 에이전트, MCP 서버, 툴, 스킬, 커스텀 리소스를 중앙에서 등록·관리·탐색할 수 있는 완전 관리형 디스커버리 서비스입니다.

> **현재 상태**: GA 출시 완료 — 신규 `agent-registry` 네임스페이스로 운영  
> **주의**: 기존 `bedrock-agentcore` 네임스페이스는 **2026년 9월 17일** 지원 종료

---

## AgentCore Registry란?

### 왜 필요한가?

조직이 AI 에이전트를 확장하면서 다음 문제가 생깁니다.

| 문제 | Registry 해결책 |
|------|----------------|
| 팀별 MCP 서버/툴이 분산 운영됨 | **중앙 카탈로그**로 통합 |
| "누가 뭘 만들었는지" 알 수 없음 | **검색/탐색** 기능 제공 |
| 같은 기능을 중복 개발함 | **Agent Sprawl 방지** |
| 보안/품질 미검증 리소스 사용 | **Curation(검토/승인)** 워크플로우 |
| AI 에이전트가 자율적으로 툴을 못 찾음 | **MCP 네이티브 엔드포인트** 제공 |

### 핵심 구성 요소

#### Registry (레지스트리)

카탈로그 컨테이너입니다. 조직 전체 단일 레지스트리 또는 타입/팀/단계별로 분리할 수 있습니다.

| 설정 항목 | 내용 |
|-----------|------|
| Name / Description | 레지스트리 식별 정보 |
| Authorization | **IAM(SigV4)** 또는 **JWT(OAuth)** 중 하나 선택 |
| Auto-approval | ON: 즉시 승인, OFF: 큐레이터 검토 후 승인 |

#### Registry Record (레코드)

등록되는 개별 리소스입니다. 타입은 다음 4가지입니다.

| 타입 | 설명 |
|------|------|
| **MCP** | MCP 프로토콜 서버 (스키마 자동 검증) |
| **AGENT** | A2A 프로토콜 에이전트 |
| **SKILL** | Markdown 문서 + 구조화 정의가 있는 스킬 |
| **CUSTOM** | 커스텀 프로토콜/메타데이터 |

### 주요 기능

| 기능 | 설명 |
|------|------|
| **하이브리드 검색** | 자연어 + 키워드 동시 검색 |
| **거버넌스 워크플로우** | Publisher → Curator 검토 → 승인/거부 |
| **MCP 네이티브 엔드포인트** | AWS SDK 없이 MCP 클라이언트로 직접 접근 |
| **레코드 자동 동기화** | 외부 MCP 서버 변경 시 메타데이터 자동 갱신 |
| **Cross-account 공유** | AWS RAM으로 다른 계정과 레지스트리 공유 |
| **EventBridge 알림** | 레코드 제출/승인/거부 시 알림 발송 |
| **CloudTrail 감사** | 모든 API 호출 로깅 |
| **PrivateLink 지원** | VPC 내부 프라이빗 라우팅 가능 |

### 인증 방식

| 방식 | 대상 | 특징 |
|------|------|------|
| **IAM (SigV4)** | 내부 AWS 팀 | boto3 자동 처리, 간단한 설정 |
| **JWT (OAuth)** | 외부 IdP 연동 | Cognito/Okta/Azure Entra ID 등, Bearer Token |

> 레지스트리당 **하나의 인증 방식만** 설정 가능하며, 생성 후 변경할 수 없습니다.

---

## 활용법

### 1. Registry 생성

```python
import boto3

# 신규 네임스페이스 사용 (권장)
client = boto3.client('agent-registry-control', region_name='us-east-1')

response = client.create_registry(
    name='my-agent-registry',
    description='조직 내 AI 에이전트 및 MCP 서버 카탈로그'
)
print(response['registryArn'])
```

**AWS CLI:**

```bash
aws agent-registry-control create-registry \
  --name "MyFirstRegistry" \
  --description "My first Agent Registry" \
  --region us-east-1
```

### 2. 레코드 등록

레코드 등록 방법은 두 가지입니다.

| 방식 | 설명 |
|------|------|
| **Synchronize from endpoint** | MCP/A2A 엔드포인트 URL 지정 → 메타데이터 자동 가져오기 |
| **Manual** | 직접 이름·타입·설명·스키마 설정 |

### 3. 레코드 제출 및 큐레이션

1. 레코드 생성 후 제출 → `pending_approval` 상태
2. 큐레이터가 검토 → `approved` / 거부
3. `approved` 상태 레코드만 검색에 노출

```
Publisher(개발자) → 레코드 제출
         ↓
Curator(큐레이터) → 검토 → 승인 / 거부
         ↓
Amazon EventBridge → 알림 발송
         ↓
Approved 레코드만 검색에 노출
```

### 4. 에이전트에서 활용하기

#### 방법 A: MCP 엔드포인트 (권장)

Registry 자체가 MCP 서버 역할을 수행하며 다음 툴을 제공합니다.

- `search_discoverable_registry_records` — 자연어 검색
- `list_discoverable_registry_records` — 페이지 목록 조회
- `batch_get_discoverable_registry_record` — ID로 상세 일괄 조회

```
# MCP 엔드포인트 URL
# 신규: https://agent-registry.<region>.api.aws/registry/<registryId>/mcp
# 구:   https://bedrock-agentcore.<region>.amazonaws.com/registry/<registryId>/mcp
```

```python
from mcp.client.streamable_http import streamablehttp_client

async with streamablehttp_client(
    url=f"https://agent-registry.us-east-1.api.aws/registry/{REGISTRY_ID}/mcp"
) as (read, write, _):
    # search / list / batch_get 툴 사용
    ...
```

#### 방법 B: AWS SDK (boto3) 직접 호출

```python
import boto3

client = boto3.client('agent-registry', region_name='us-east-1')

response = client.search_discoverable_registry_records(
    registryId='my-registry-id',
    searchQuery='배송 추적 MCP 서버',
    maxResults=10,
    filters={'recordType': 'MCP'}
)

for record in response['records']:
    print(f"이름: {record['name']}")
    print(f"URL: {record.get('mcpServerUrl')}")
    print(f"툴 스키마: {record.get('toolSchemas')}")
```

### 5. 에이전트 활용 3단계 패턴

```
① Registry 탐색  →  ② MCP 서버 연결  →  ③ 툴 실행
   (Discovery)        (Connection)         (Execution)

Registry는 "지도" 역할 → 실제 실행은 레코드의 URL로 직접 연결
```

#### 동적 MCP 연결 패턴

```python
import boto3
from mcp.client.streamable_http import streamablehttp_client

# Step 1: Registry에서 검색
registry_client = boto3.client('agent-registry')
results = registry_client.search_discoverable_registry_records(
    registryId=REGISTRY_ID,
    searchQuery='delivery tracking'
)

# Step 2: 레코드에서 MCP URL 추출
mcp_url = results['records'][0]['mcpServerUrl']

# Step 3: 실제 MCP 서버에 연결 + 툴 호출
async with streamablehttp_client(url=mcp_url) as (read, write, _):
    tools = await client.list_tools()
    result = await client.call_tool("track_delivery", {"order_id": "12345"})
```

#### AgentCore Harness 통합

**방법 A — `remote_mcp`로 Registry MCP 등록**

```python
harness_config = {
    "tools": [{
        "type": "remote_mcp",
        "url": f"https://agent-registry.us-east-1.api.aws/registry/{REGISTRY_ID}/mcp"
    }]
}
```

**방법 B — System Prompt에서 동적 탐색 지시**

```python
system_prompt = """
작업 수행 전에 search_discoverable_registry_records를 호출하여
관련 MCP 서버나 툴을 먼저 탐색하고,
가장 적합한 리소스를 찾아 사용하세요.
"""
```

AgentCore Harness + LangGraph + MCP 아키텍처에서는 Registry를 `remote_mcp` 툴로 Harness에 등록해 두면, 에이전트가 필요한 서비스를 하드코딩 없이 자율적으로 탐색할 수 있습니다. 멀티에이전트 환경에서 "어떤 에이전트/툴이 있는지" 자동 발견하는 데 핵심 요소가 됩니다.

---

## 주요 사용 사례

| 시나리오 | 활용 방법 |
|----------|-----------|
| 내부 워크플로우 구축 | Registry에서 HR/PTO/문서 서비스 검색 → 수 분 내 연결 |
| 에이전트 기능 확장 | 배송 추적 기능 필요 시 Registry 검색 → Gateway에 추가 |
| Agent Sprawl 방지 | 관리자가 중복 개발 발견 → 두 팀 연결하여 통합 |
| 스킬 공유 | PDF 추출 스킬 등록 → 전사 팀에서 재사용 |
| 품질 기준 적용 | EventBridge 트리거 → 자동 검토 파이프라인 실행 |
| 자동 동기화 | MCP 서버 URL 지정 → 툴 변경 시 자동 메타데이터 갱신 |

---

## 마이그레이션 체크리스트

| 항목 | 변경 내용 |
|------|-----------|
| 네임스페이스 | `bedrock-agentcore` → `agent-registry` |
| CLI 명령어 | `aws bedrock-agentcore-control` → `aws agent-registry-control` |
| 콘솔 URL | `bedrock-agentcore` console → `agent-registry` console |
| **지원 종료일** | **2026년 9월 17일** |

---

## Reference

1. [AWS Agent Registry Release Notes](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/release-notes.html)
2. [Registry 개요](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry.html)
3. [Registry Get Started](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-get-started.html)
4. [CreateRegistry API](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CreateRegistry.html)
5. [Agentic AI Lens — Agent Registry BP](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentrel04-bp02.html)
6. [AgentCore Gateway](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentops04-bp01.html)
7. [AgentCore Gateway Target](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec02-bp03.html)
