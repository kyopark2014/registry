# AgentCore Registry 활용하기

AWS Agent Registry(AgentCore Registry)는 조직 내 AI 에이전트, MCP 서버, 툴, 스킬, 커스텀 리소스를 중앙에서 등록·관리·탐색할 수 있는 완전 관리형 디스커버리 서비스입니다.

> **현재 상태**: GA 출시 완료 — 신규 `agent-registry` 네임스페이스로 운영  
> **주의**: 기존 `bedrock-agentcore` 네임스페이스는 **2026년 9월 17일** 지원 종료  
> **비용**: 사용량 기반 + 월 Free Tier — 자세한 내용은 [비용 (Pricing)](#비용-pricing) 참고


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
| **하이브리드 검색** | 자연어(시맨틱) + 키워드를 **동시에** 실행 후 가중치로 랭킹 |
| **거버넌스 워크플로우** | Publisher → Curator 검토 → 승인/거부/폐기; EventBridge로 기존 승인망 연동 |
| **MCP 네이티브 엔드포인트** | AWS SDK 없이 MCP 클라이언트로 직접 접근 |
| **레코드 자동 동기화** | 외부 MCP/A2A URL에서 이름·툴 스키마 등 메타데이터 pull (IAM/OAuth) |
| **Org 자동 감지** | Organizations 사용 시 Runtime·Gateway를 멤버 계정에서 자동 카탈로그 |
| **Cross-account 공유** | AWS RAM으로 다른 계정과 레지스트리 공유 |
| **EventBridge 알림** | 승인 요청 등 → Lambda/SNS/SQS/Step Functions |
| **CloudTrail 감사** | Control plane API 호출 로깅 (management events) |
| **PrivateLink 지원** | VPC 내부 프라이빗 라우팅 가능 |

### 인증 방식

| 방식 | 대상 | 특징 |
|------|------|------|
| **IAM (SigV4)** | 내부 AWS 팀 | boto3 자동 처리, 간단한 설정 |
| **JWT (OAuth)** | 외부 IdP 연동 | Cognito/Okta/Azure Entra ID 등, Bearer Token |

> 레지스트리당 **하나의 인증 방식만** 설정 가능하며, 생성 후 변경할 수 없습니다.

### 한눈에 보는 동작 흐름

```
Administrator ──► Registry 생성 (권한·승인 정책 설정)
                      │
Publisher ──────────► 레코드 작성/동기화 → 승인 요청
                      │
Curator ────────────► 검토 → Approve / Reject / Deprecate
                      │                    │
                      │              EventBridge 알림
                      ▼                    │
Consumer (사람·에이전트) ◄── Search / List / Get / MCP endpoint
```

| 페르소나 | 역할 |
|----------|------|
| **Administrator** | Registry 생성·조직 방식·인증·승인 정책·IAM·EventBridge 연동 |
| **Publisher** | MCP/Agent/Skill 등을 레코드로 등록·Draft 수정·승인 요청·URL sync |
| **Curator** | 보안/품질 기준 검토 후 승인·거부·폐기(deprecate) |
| **Consumer** | 승인된 레코드만 검색·목록·MCP로 발견 (사람 또는 AI 에이전트) |

### Control plane vs Data plane

| 구분 | 용도 | 인증 | 엔드포인트 예 |
|------|------|------|----------------|
| **Control plane** | Registry/레코드 생성·수정·승인 상태 변경 | **항상 IAM** | `agent-registry-control.{region}.api.aws` |
| **Data plane** | 검색·목록·상세 조회·MCP invoke | Registry 설정의 IAM 또는 JWT | `agent-registry.{region}.api.aws` |

> 검색/MCP용 inbound 인증을 JWT로 바꿔도, **관리 API(Control plane)는 계속 IAM**입니다.

### 인바운드 vs 아웃바운드 인증

| 종류 | 의미 |
|------|------|
| **Inbound** | Consumer가 Registry를 검색·MCP 호출할 때 (IAM 또는 JWT) |
| **Outbound** | Registry가 외부 MCP/A2A URL에서 메타데이터를 **동기화**할 때 (IAM role 또는 OAuth credential provider) |

이 프로젝트의 `register_mcp.py`는 outbound로 harness-work Runtime MCP URL을 sync합니다.  
`register_agent.py`는 Harness 메타데이터로 AGENT(`a2aAgentCard`) 레코드를 수동 등록합니다.

### 레코드·버전·상태 (이해하기)

- `name` + `recordVersion` 조합이 Registry 내 **유일 키**입니다. 같은 이름에 버전을 올려 개정합니다.
- Sync로 기존 레코드를 갱신하면 **새 revision**이 생깁니다.
- 대략적인 생명주기: `Draft` → 승인 요청 → `Pending` → `Approved`(검색 노출) / `Rejected` → 필요 시 `Deprecated`(더 이상 검색 안 됨)
- Auto-approval이어도 **제출(Submit) API 호출**은 필요합니다.

### 탐색 방법 3가지

| 방법 | API / 경로 | 언제 쓰나 |
|------|------------|-----------|
| **Hybrid Search** | `SearchDiscoverableRegistryRecords` | 자연어(“비행기 예약 툴”) + 키워드 동시 |
| **Catalog Browse** | `List` / `Get` / `BatchGet` | 디렉터리형 UI·필터·일괄 상세 |
| **MCP endpoint** | `…/registry/<id>/mcp` | IDE·에이전트가 SDK 없이 MCP로 발견 |

Approved 레코드만 Data plane / MCP에 노출됩니다.

### 관련 AWS 서비스

| 서비스 | Registry와의 관계 |
|--------|-------------------|
| **AgentCore Runtime** | 등록할 MCP/에이전트 호스팅 |
| **AgentCore Gateway** | API/Lambda → MCP 도구화 후 등록 |
| **AgentCore Identity** | JWT inbound용 IdP/자격 증명 |
| **EventBridge** | 승인 요청 등 이벤트 → 기존 검토 파이프라인 연동 |
| **CloudTrail** | Control plane API 감사 로그 |
| **AWS RAM / Organizations** | 계정 간 공유, Org 내 Runtime·Gateway **자동 감지(auto-detect)** |
| **PrivateLink** | VPC 프라이빗 접속 |

### 리전·가용성

AgentCore가 제공되는 리전에서 사용합니다. 대표적으로 **us-west-2**(이 프로젝트 기본), us-east-1, eu-west-1, ap-northeast-1, ap-southeast-2 등. 최신 목록은 [Supported Regions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html)를 확인하세요.


---

## 프로젝트 구조

```
registry/
├── README.md
├── requirements.txt          # boto3/botocore >= 1.43.84
├── application/
│   └── config.json           # 생성 결과 (registry_id, mcp/agent record 등)
└── src/
    ├── registry.py           # Registry 생성 스크립트
    ├── register_mcp.py       # harness-work KB MCP 레코드 등록
    ├── register_agent.py     # harness_work Harness AGENT 레코드 등록
    ├── test_registry_mcp.py  # Registry 검색 → MCP retrieve E2E
    └── test_registry_agent.py # Registry 검색 → InvokeHarness E2E
```

---

## 사전 요구사항

| 항목 | 요구 사항 |
|------|-----------|
| **boto3 / botocore** | **≥ 1.43.84** (`agent-registry-control`, `agent-registry` 포함) |
| **권장 리전** | `us-west-2` (이 프로젝트 기본값) |
| **AWS 자격 증명** | Registry 생성·조회 권한이 있는 IAM 역할/사용자 |

```bash
pip install -r requirements.txt
# 또는
pip install 'boto3>=1.43.84' 'botocore>=1.43.84'
```

`boto3 < 1.43.84`에서는 `agent-registry-control` 서비스가 없어 preview 네임스페이스(`bedrock-agentcore-control`)로만 동작합니다.

---

## 활용법

### 0. 이 프로젝트에서 Registry 생성하기

`project_name = "registry"` 기준으로 Agent Registry를 생성합니다. GA `agent-registry-control` 엔드포인트를 우선 사용하고, SDK가 구버전이면 preview로 fallback합니다.

```bash
pip install -r requirements.txt
python3 src/registry.py
```

생성 결과는 `application/config.json`에 저장됩니다.

| 설정 | 값 |
|------|-----|
| `projectName` / Registry Name | `registry` |
| Region | `us-west-2` |
| Authorizer | `AWS_IAM` (`discoveryConfiguration`) |
| Auto-approval | `APPROVE_ALL` |
| Control endpoint | `https://agent-registry-control.us-west-2.api.aws` |
| MCP URL | `https://agent-registry.us-west-2.api.aws/registry/<registryId>/mcp` |

### 0-1. Knowledge Base MCP 레코드 등록

`harness-work`의 AgentCore Runtime MCP(`knowledge_base_of_harness_work`)를 Registry에 등록합니다. Runtime 존재 여부를 확인한 뒤, IAM URL sync로 메타데이터/툴 스키마를 가져오고 승인까지 수행합니다.

```bash
python3 src/register_mcp.py
```

| 항목 | 값 |
|------|-----|
| Runtime | `knowledge_base_of_harness_work` |
| Record name | `knowledge-base-of-harness-work` |
| Sync role | `role-agent-registry-sync-for-registry` |
| Tool | `retrieve(keyword, actor_id)` |

### 0-2. Harness Agent (`harness_work`) 레코드 등록

`src/register_agent.py`가 `harness-work/application/config.json`의 `harnessId` / `HARNESS_ARN`을 읽고, Control Plane `GetHarness`로 상태를 확인한 뒤 **AGENT** 레코드를 만듭니다.

```bash
python3 src/register_agent.py
```

#### 등록 흐름 (`register_agent.py`)

```
[1/3] Ensure Registry (registry.py / register_mcp._ensure_registry)
         │
      GetHarness(harnessId) — status=READY 확인, live ARN 사용
         │
      A2A Agent Card 생성 (_build_agent_card)
         │  name=harnessName (harness_work)
         │  url / metadata.harnessArn = HARNESS_ARN
         │  skills, tools, modelId, region …
         │
[2/3] create_registry_record(recordType=AGENT, descriptors.a2aAgentCard)
         │  동일 name 이 있으면 update_registry_record 로 descriptors 갱신
         │
[3/3] submit_registry_record_for_approval → APPROVED (Auto-approval)
         │
      application/config.json 에 harness_agent_* 키 기록
```

| 항목 | 코드 기준 값 |
|------|----------------|
| 소스 config | `../harness-work/application/config.json` |
| `RECORD_NAME` | `harness-work` |
| `RECORD_DISPLAY_NAME` | `Harness Agent (harness_work)` |
| `recordType` | `AGENT` |
| `recordVersion` | `1.0.0` |
| descriptors | `a2aAgentCard.data` + `dataSchemaVersion=0.3.0` |
| Agent Card `url` | Harness ARN (HTTP A2A가 아님 — discovery→`InvokeHarness` 규약) |
| Agent Card `metadata` | `invokeApi=bedrock-agentcore:InvokeHarness`, `harnessArn`, `harnessId`, `tools`, `modelId` … |
| 재실행 | name 기준 idempotent — 기존 레코드면 descriptors 업데이트 후 승인 상태 유지 |

`config.json`에 추가되는 키 예:

| 키 | 의미 |
|----|------|
| `harness_agent_record_name` | `harness-work` |
| `harness_agent_record_id` / `_arn` / `_status` | Registry 레코드 |
| `harness_agent_name` | `harness_work` |
| `harness_agent_harness_id` / `_arn` | Invoke 대상 |
| `harness_agent_region` | 리전 |

### 0-3. E2E 테스트

**MCP (KB retrieve)** — [Registry Test — MCP](#registry-test--mcp-test_registry_mcppy) 참고:

```bash
python3 src/test_registry_mcp.py --query "보일러 에러 코드" --actor-id ksdyb
```

**Harness Agent (InvokeHarness)** — [Registry Test — Agent](#registry-test--agent-test_registry_agentpy) 참고:

```bash
python3 src/test_registry_agent.py
python3 src/test_registry_agent.py --prompt "한 문장으로 자기소개만 해줘." --actor-id ksdyb
python3 src/test_registry_agent.py --search "harness_work"
```

### 1. Registry 생성 (SDK / CLI)

GA 네임스페이스는 기본 엔드포인트가 아직 `.amazonaws.com`으로 잡힐 수 있으므로, **`.api.aws` endpoint_url을 명시**하는 것을 권장합니다.

```python
import boto3

client = boto3.client(
    "agent-registry-control",
    region_name="us-west-2",
    endpoint_url="https://agent-registry-control.us-west-2.api.aws",
)

response = client.create_registry(
    name="registry",
    description="조직 내 AI 에이전트 및 MCP 서버 카탈로그",
    discoveryConfiguration={"authorizerType": "AWS_IAM"},
    approvalConfiguration={"autoApprovalRules": ["APPROVE_ALL"]},
)
print(response["registryArn"])
```

**AWS CLI:**

```bash
aws agent-registry-control create-registry \
  --name "registry" \
  --description "My first Agent Registry" \
  --discovery-configuration '{"authorizerType":"AWS_IAM"}' \
  --approval-configuration '{"autoApprovalRules":["APPROVE_ALL"]}' \
  --region us-west-2 \
  --endpoint-url https://agent-registry-control.us-west-2.api.aws
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

> Auto-approval이 ON이면 제출 직후 Approved로 전환됩니다. 그래도 `SubmitRegistryRecordForApproval` 호출은 필요합니다.

### 4. 에이전트에서 활용하기

#### 방법 A: MCP 엔드포인트 (권장)

Registry 자체가 MCP 서버 역할을 수행하며 다음 툴을 제공합니다.

- `search_discoverable_registry_records` — 자연어 검색
- `list_discoverable_registry_records` — 페이지 목록 조회
- `batch_get_discoverable_registry_record` — ID로 상세 일괄 조회

```
# MCP 엔드포인트 URL
# 신규(GA): https://agent-registry.<region>.api.aws/registry/<registryId>/mcp
# 구(preview): https://bedrock-agentcore.<region>.amazonaws.com/registry/<registryId>/mcp
```

```python
from mcp.client.streamable_http import streamablehttp_client

async with streamablehttp_client(
    url=f"https://agent-registry.us-west-2.api.aws/registry/{REGISTRY_ID}/mcp"
) as (read, write, _):
    # search / list / batch_get 툴 사용
    ...
```

#### 방법 B: AWS SDK (boto3) 직접 호출

```python
import boto3

client = boto3.client(
    "agent-registry",
    region_name="us-west-2",
    endpoint_url="https://agent-registry.us-west-2.api.aws",
)

response = client.search_discoverable_registry_records(
    registryId="my-registry-id",
    searchQuery="배송 추적 MCP 서버",
    maxResults=10,
    filters={"recordType": "MCP"},
)

for record in response["records"]:
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
registry_client = boto3.client(
    "agent-registry",
    region_name="us-west-2",
    endpoint_url="https://agent-registry.us-west-2.api.aws",
)
results = registry_client.search_discoverable_registry_records(
    registryId=REGISTRY_ID,
    searchQuery="delivery tracking",
)

# Step 2: 레코드에서 MCP URL 추출
mcp_url = results["records"][0]["mcpServerUrl"]

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
        "url": f"https://agent-registry.us-west-2.api.aws/registry/{REGISTRY_ID}/mcp"
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

## 비용 (Pricing)

출처: [Amazon Bedrock AgentCore Pricing — AWS Agent Registry](https://aws.amazon.com/bedrock/agentcore/pricing/)  
요금은 **사용량 기반**이며 선수금·최소 요금이 없습니다. **매월 Free Tier**를 넘긴 부분만 과금됩니다.

### Free Tier (매월)

| 항목 | 월 무료 한도 |
|------|----------------|
| Registry **Records** (순 보유량) | 처음 **5,000**개 |
| **Search** API 호출 | 처음 **1,000,000**회 |
| **List + Get** API 호출 (합산) | 처음 **2,000,000**회 |

### 초과 요금

| 청구 단위 | 단가 (Free Tier 초과분) |
|-----------|-------------------------|
| Registry Records | **$0.400 / 1,000 records** |
| Search API Invocation | **$0.020 / 1,000 invocations** |
| List and Get API Invocations | **$0.004 / 1,000 invocations** |

### 과금 시 알아둘 점

- **Net Records**: 시점 기준 **현재 남아 있는** 레코드 수만 집계합니다. 추가 후 삭제하면 과금 대상에서 빠집니다.
- **재판매(resell)** 용도로 End User에게 Registry를 제공하는 고객은 Free Tier 대상이 **아니며**, 첫 레코드/호출부터 위 단가가 적용됩니다.
- Registry 자체 요금과 **별도**: Runtime·Gateway·Bedrock 모델·Knowledge Base·CloudWatch 등은 각 서비스 요금이 추가됩니다. (이 프로젝트 E2E에서 MCP `retrieve`는 Runtime/KB 쪽이 별도 과금)

### 요금 예시 (공식 페이지 요약)

금융사 사내 툴 마켓플레이스 시나리오:

| 단계 | 대략 규모 | 월 예상 |
|------|-----------|---------|
| 초기 | 유효 레코드 ~9,500 / Search·List·Get는 Free Tier 이내 | Records 초과분만 **약 $1.80** |
| 성장 | 레코드 17,000 + Search 3M + List/Get 7.5M | **약 $66.80** |

계산 스케치 (성장 월):

- Records: (17,000 − 5,000) × $0.40/1,000 ≈ **$4.80**
- Search: (3,000,000 − 1,000,000) × $0.020/1,000 = **$40.00**
- List+Get: (7,500,000 − 2,000,000) × $0.004/1,000 = **$22.00**

> 이 저장소처럼 레코드 수·테스트 Search가 적은 환경에서는 **사실상 Free Tier 안**에서 끝나는 경우가 많습니다. 운영 규모가 커지면 Search 호출이 비용의 대부분을 차지하기 쉽습니다.

요금은 변경될 수 있으니 배포 전 [공식 Pricing](https://aws.amazon.com/bedrock/agentcore/pricing/)을 다시 확인하세요.

---

## Registry Test

이 프로젝트는 Registry Discovery 후 실제 호출까지 검증하는 E2E 스크립트를 **두 종류** 제공합니다.

| 스크립트 | 대상 레코드 | Execution |
|----------|-------------|-----------|
| `src/test_registry_mcp.py` | MCP (`knowledge-base`) | Runtime MCP `retrieve` |
| `src/test_registry_agent.py` | AGENT (`harness-work` / `harness_work`) | `InvokeHarness` 스트리밍 |

---

## Registry Test — MCP (`test_registry_mcp.py`)

`src/test_registry_mcp.py`는 Agent Registry에 등록된 Knowledge Base MCP를 **탐색 → 연결 → 실행**하는 E2E 검증 스크립트입니다. 에이전트가 실제로 쓸 3단계 패턴(Discovery → Connection → Execution)을 로컬에서 재현합니다.

### 목적

| 검증 항목 | 내용 |
|-----------|------|
| Data plane 검색 | GA `agent-registry`의 `SearchDiscoverableRegistryRecords`가 APPROVED 레코드를 반환하는지 |
| 레코드 메타데이터 | MCP URL·툴 스키마(`retrieve`)가 descriptors에서 추출되는지 |
| Runtime 호출 | AgentCore Runtime MCP URL에 SigV4로 접속해 툴을 실행할 수 있는지 |
| RAG 질의 | `retrieve(keyword, actor_id)`로 Knowledge Base 검색이 동작하는지 |

### 사전 조건

1. `pip install -r requirements.txt` (boto3 ≥ 1.43.84) 및 `mcp`, `httpx`
2. `python3 src/registry.py`로 GA Registry 생성 완료
3. `python3 src/register_mcp.py`로 harness-work KB MCP 레코드 등록·승인 완료
4. `application/config.json`에 `registry_id`, `kb_mcp_runtime_arn`, `kb_mcp_url` 등이 채워져 있음
5. AWS 자격 증명에 Registry 검색 + (필요 시) Runtime `InvokeAgentRuntime` 권한

### 실행 방법

```bash
# 기본값: search="knowledge base retrieve RAG", query="보일러 에러 코드", actor-id=ksdyb
python3 src/test_registry_mcp.py

# 질의/actor 지정
python3 src/test_registry_mcp.py --query "보일러 에러 코드" --actor-id ksdyb

# Registry 검색어만 변경
python3 src/test_registry_mcp.py --search "knowledge base" --query "보일러 에러 코드"

# Runtime resource policy 자동 업데이트를 건너뛸 때
python3 src/test_registry_mcp.py --skip-runtime-policy
```

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `--search` | `knowledge base retrieve RAG` | Registry 하이브리드 검색 질의 (레코드 발견용) |
| `--query` | `보일러 에러 코드` | MCP `retrieve`에 넘기는 keyword |
| `--actor-id` | `ksdyb` | KB owner 필터용 계정 로그인 ID |
| `--skip-runtime-policy` | off | 호출자 IAM을 Runtime resource policy에 자동 추가하지 않음 |

### 동작 흐름

```
[1/3] SearchDiscoverableRegistryRecords
         │  registryIds=[config.registry_id]
         │  searchQuery=--search
         ▼
      APPROVED MCP 레코드 선택 (retrieve 툴 또는 knowledge 이름 우선)
         │
[2/3] descriptors.mcpServer 에서 streamable-http URL 추출
         │  (없으면 config.kb_mcp_url 사용)
         │
      (선택) Runtime resource policy에 현재 IAM principal 추가
         │
      httpx AsyncClient에 SigV4 패치 적용
         │  bedrock-agentcore → service=bedrock-agentcore
         │  agent-registry     → service=agent-registry
         ▼
[3/3] MCP ClientSession
         │  initialize → list_tools → call_tool("retrieve", {keyword, actor_id})
         ▼
      결과 출력
         │
      결과가 [] 이면 unfiltered Bedrock retrieve fallback (데모용)
```

### Step 상세

#### 1) Registry 검색 (Discovery)

- 클라이언트: `boto3.client("agent-registry", endpoint_url="https://agent-registry.{region}.api.aws")`
- API: `search_discoverable_registry_records(registryIds=[...], searchQuery=...)`
- **APPROVED** 상태 레코드만 반환됩니다 (Draft/Pending는 검색에 안 나옴)
- 예시 매칭: `Knowledge Base MCP (harness-work)` / name=`knowledge-base` / tools=`['retrieve']`

#### 2) MCP URL 추출 및 인증 준비 (Connection)

레코드 `descriptors.mcpServer`에서 다음 순으로 URL을 읽습니다.

1. `data` (server.json) → `remotes[].url` (streamable-http)
2. `source.fromUrl.url` (sync 원본)
3. fallback: `application/config.json`의 `kb_mcp_url`

AgentCore Runtime MCP는 IAM(SigV4)이 필요합니다. 표준 MCP 클라이언트는 서명을 하지 않으므로, 스크립트가 `httpx.AsyncClient` request hook으로 본문 포함 SigV4 서명을 붙입니다.

또한 Runtime resource policy에 현재 호출자(`iam:user/...` 또는 assumed-role의 role ARN)가 없으면 `PutResourcePolicy`로 추가합니다. 이미 허용된 principal이면 건너뜁니다. (`--skip-runtime-policy`로 비활성화 가능)

#### 3) 툴 실행 (Execution)

```text
retrieve(keyword="보일러 에러 코드", actor_id="ksdyb")
```

- harness-work KB MCP는 Bedrock Knowledge Base retrieve 시 metadata `owner`에 대해 `listContains(actor_id)` 필터를 적용합니다.
- 문서가 `docs/{actor_id}/` 아래에 있거나 ingestion 시 `owner`에 해당 ID가 있어야 히트가 납니다.

### 결과 해석

| 상황 | 의미 |
|------|------|
| Registry 검색에 레코드 1건 이상 | Discovery / APPROVED 게시 정상 |
| `MCP tools: ['retrieve']` | Runtime MCP 연결·프로토콜 협상 정상 |
| retrieve가 문서 hit 반환 | actor_id 필터와 RAG 질의 정상 |
| retrieve가 `[]` | 권한/연결은 됐지만 **owner 필터에 맞는 문서 없음** |

현재 harness-work 샘플 문서 `docs/error_code.pdf`는 owner 메타데이터가 없어 `actor_id=ksdyb` MCP 호출은 `[]`가 될 수 있습니다. 이 경우 스크립트는 경고를 남기고 **필터 없는 Bedrock `retrieve`** 로 같은 질의(`보일러 에러 코드`)를 다시 호출해, KB에 보일러 에러코드(A001, A002 등) 콘텐츠가 있음을 확인합니다.

MCP 경로에서도 hit를 받으려면:

1. 문서를 `docs/{actor_id}/...`에 업로드하거나
2. ingestion 시 metadata `owner`에 해당 actor를 넣고
3. Knowledge Base 동기화 후 다시 테스트하세요.

### 결과 포맷과 예

실행 로그는 크게 **진행 로그(INFO)** 와 **최종 JSON 결과** 두 부분으로 나뉩니다.

#### 1) 진행 로그 포맷

| 단계 | 로그 예시 | 의미 |
|------|-----------|------|
| 시작 | `Registry ID: lNOP***ShoC` | 검색 대상 Registry |
| | `Search: knowledge base retrieve RAG` | Discovery 검색어 (`--search`) |
| | `Retrieve query: 보일러 에러 코드` | MCP keyword (`--query`) |
| | `Actor ID: ksdyb` | owner 필터 (`--actor-id`) |
| [1/3] | `1) Knowledge Base MCP (harness-work) (knowledge-base) type=MCP tools=['retrieve']` | 검색된 APPROVED 레코드 |
| [2/3] | `Selected record: knowledge-base` | 실행 대상 레코드 |
| | `MCP URL: https://bedrock-agentcore..../invocations?qualifier=DEFAULT` | Runtime MCP 엔드포인트 |
| | `Runtime resource policy already allows arn:aws:iam::...:user/...` | Invoke 권한 확인 |
| | `Applied httpx SigV4 patch ...` | SigV4 서명 패치 적용 |
| [3/3] | `Calling retrieve(keyword='보일러 에러 코드', actor_id='ksdyb')` | 툴 호출 시작 |
| | `Negotiated protocol version: 2025-11-25` | MCP 프로토콜 협상 성공 |
| | `MCP tools: ['retrieve']` | Runtime이 노출한 툴 목록 |
| | `HTTP/1.1 405` / `Session termination failed: 404` | AgentCore Runtime의 GET/DELETE 미지원 경고 (호출 자체는 정상일 수 있음) |

#### 2) MCP retrieve 결과 포맷

`actor_id` owner 필터를 통과한 hit가 있을 때:

```json
[
  {
    "contents": "문서 청크 텍스트...",
    "reference": "https://.../docs/..."
  }
]
```

owner 필터에 맞는 문서가 없으면:

```json
[]
```

이어서 아래 경고와 fallback이 출력됩니다.

```text
WARNING - MCP retrieve returned no hits for actor_id='ksdyb'.
          harness-work KB filters by metadata owner;
          docs without owner (e.g. docs/error_code.pdf) are excluded.
INFO    - Fallback: unfiltered Bedrock retrieve for demo
```

#### 3) Bedrock fallback 결과 포맷

필터 없이 KB를 직접 조회한 결과입니다. 각 원소는 다음 필드를 가집니다.

| 필드 | 타입 | 설명 |
|------|------|------|
| `score` | number | 유사도 점수 (높을수록 관련도 높음) |
| `contents` | string | 검색된 텍스트 청크 |
| `metadata` | object | Bedrock KB 메타데이터 (`chunk-id`, `page-number`, `data-source-id` 등) |

#### 4) 실제 실행 예 (`python test_registry_mcp.py`)

```text
============================================================
Agent Registry E2E Test
============================================================
Registry ID: lNOP***ShoC
Search: knowledge base retrieve RAG
Retrieve query: 보일러 에러 코드
Actor ID: ks***
[1/3] Searching discoverable registry records
  1) Knowledge Base MCP (harness-work) (knowledge-base) type=MCP tools=['retrieve']
[2/3] Selected record: knowledge-base
  MCP URL: https://bedrock-agentcore.us-west-2.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-west-2%3A************%3Aruntime%2Fknowledge_base_of_harness_work-**********/invocations?qualifier=DEFAULT
Runtime resource policy already allows arn:aws:iam::************:user/********
Applied httpx SigV4 patch for AgentCore / Agent Registry MCP
[3/3] Calling retrieve(keyword='보일러 에러 코드', actor_id='ks***')
Negotiated protocol version: 2025-11-25
MCP tools: ['retrieve']
============================================================
Retrieve result (MCP / actor_id filter)
============================================================
[]
WARNING - MCP retrieve returned no hits for actor_id='ks***'...
INFO    - Fallback: unfiltered Bedrock retrieve for demo
```

Fallback JSON 예 (일부):

```json
[
  {
    "score": 0.9225585162639618,
    "contents": "보일러 에러코드   • A001 - 점화 에러 입니다.   • A002 - 의사 화염 에러입니다.   • A003 - 실화 에러입니다.   • A106 - FAN 회전수 미감지 일 때 나타납니다.   • A107 - FAN 회전수 기준 초과일 때 나타나는 에러입니다.   • A110 - FAN 회전수 기준 미달일 때 나타나는 에러입니다.   • A204 - 수온 센서 이상 에러입니다.   • A205 - 과열 센서 이상입니다.   • A214- 출탕센터 이상일 때 나타납니다.   • A294 - 수위봉 이상일때 나타나는 에러입니다.   • A297 - 가스누설 이상일 때 나타나는 에러입니다.   • A396 - 과열센서에 이상이 생길 때 나는 에러입니다.   • A398 - 배기과열에 이상이 생기면 나타납니다.   • A399 - 수온센서 이상에 나타나는 에러입니다",
    "metadata": {
      "x-amz-bedrock-kb-source-file-modality": "TEXT",
      "x-amz-bedrock-kb-document-page-number": 1.0,
      "x-amz-bedrock-kb-chunk-id": "f1d6****-****-****-****-********9f06",
      "x-amz-bedrock-kb-data-source-id": "CG1*****CL"
    }
  },
  {
    "score": 0.8969638347625732,
    "contents": "• A396 - 과열센서에 이상이 생길 때 나는 에러입니다.   • A398 - 배기과열에 이상이 생기면 나타납니다.   • A399 - 수온센서 이상에 나타나는 에러입니다.   • A491 - 단수 확인 에러입니다.   • A495 - 저수위 감지 에러입니다.   • A508 - 통신 이상 에러입니다.   • A621 - 지진 감지 에러입니다.   • A622 - 응축구 배출구가 막혔을 때 나오는 에러입니다.   • A632 - 믹싱 밸브 이상 에러입니다.   • A646 - 가스 밸브 이상일 때 에러입니다.   • A740 - 딥스위치에 이상이 생긴 에러입니다.보일러 에러 해결 방법   ...",
    "metadata": {
      "x-amz-bedrock-kb-source-file-modality": "TEXT",
      "x-amz-bedrock-kb-document-page-number": 1.0,
      "x-amz-bedrock-kb-chunk-id": "9b9d****-****-****-****-********e708",
      "x-amz-bedrock-kb-data-source-id": "CG1*****CL"
    }
  }
]
```

이 예에서:

- **Discovery/Connection/Execution 경로 자체는 성공** (레코드 발견 → SigV4 MCP 연결 → `retrieve` 호출)
- MCP 결과는 owner 필터로 `[]`
- Fallback으로 KB에 `보일러 에러코드`(A001, A002, …) 콘텐츠가 존재함을 확인

---

## Registry Test — Agent (`test_registry_agent.py`)

`src/test_registry_agent.py`는 Registry에 등록된 **harness_work Harness AGENT**를 **탐색 → ARN 추출 → InvokeHarness** 하는 E2E 스크립트입니다. (`register_agent.py`로 등록한 `harness-work` 레코드 대상)

### 목적

| 검증 항목 | 내용 |
|-----------|------|
| Data plane 검색 | `SearchDiscoverableRegistryRecords`가 APPROVED **AGENT** 레코드를 반환하는지 |
| Agent Card | `descriptors.a2aAgentCard`에서 Harness ARN·설명을 읽는지 |
| Harness 호출 | `bedrock-agentcore:InvokeHarness` 스트리밍이 동작하는지 |

### 사전 조건

1. `pip install -r requirements.txt` (boto3 ≥ 1.43.84)
2. `python3 src/registry.py`로 GA Registry 생성 완료
3. `python3 src/register_agent.py`로 harness_work AGENT 레코드 등록·승인 완료
4. `application/config.json`에 `registry_id`, `harness_agent_harness_arn` 등이 채워져 있음 (또는 `../harness-work/application/config.json`의 `HARNESS_ARN`)
5. AWS 자격 증명에 Registry 검색 + `bedrock-agentcore:InvokeHarness` 권한

### 실행 방법

```bash
# 기본값: search="harness_work AgentCore Harness assistant",
#         prompt="짧게 자기소개하고, 어떤 도구를 쓸 수 있는지 한 줄로 말해줘.", actor-id=ksdyb
python3 src/test_registry_agent.py

# 프롬프트 / actor 지정
python3 src/test_registry_agent.py --prompt "한 문장으로 자기소개만 해줘." --actor-id ksdyb

# Registry 검색어만 변경
python3 src/test_registry_agent.py --search "harness_work"

# 세션 ID 고정 (대화 이어가기)
python3 src/test_registry_agent.py --session-id "1234abcd-12ab-34cd-56ef-1234567890ab"
```

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `--search` | `harness_work AgentCore Harness assistant` | Registry 하이브리드 검색 (AGENT 레코드 발견) |
| `--prompt` | 자기소개+도구 한 줄 요약 | `InvokeHarness` user 메시지 |
| `--actor-id` | `ksdyb` | `InvokeHarness` `actorId` (Memory 격리) |
| `--session-id` | 랜덤 UUID | `runtimeSessionId` |

### 동작 흐름 (`test_registry_agent.py`)

```
[1/3] SearchDiscoverableRegistryRecords
         │  searchQuery=--search
         ▼
      pick_harness_record — recordType=AGENT + name/display에 harness 우선
         │
[2/3] extract_harness_arn
         │  1) agent card metadata.harnessArn
         │  2) agent card url (arn:aws:bedrock-agentcore:…:harness/…)
         │  3) config.harness_agent_harness_arn
         │  4) harness-work config HARNESS_ARN
         ▼
[3/3] invoke_harness(harnessArn, actorId, messages=[{role:user, content:[{text}]}])
         │  contentBlockDelta 텍스트 스트리밍 출력
         ▼
      messageStop / usage 토큰 로그
```

### 실제 실행 예 (`python test_registry_agent.py`)

```text
============================================================
Agent Registry → Harness Agent E2E Test
============================================================
Registry ID: lNOP***ShoC
Search: harness_work AgentCore Harness assistant
Actor ID: ks***
Session ID: 85d2****-****-****-****-********9ab0
Prompt: 짧게 자기소개하고, 어떤 도구를 쓸 수 있는지 한 줄로 말해줘.
[1/3] Searching discoverable registry records
  1) Harness Agent (harness_work) (harness-work) type=AGENT card.name=harness_work
  2) Knowledge Base MCP (harness-work) (knowledge-base) type=MCP card.name=None
[2/3] Selected record: harness-work
  Harness ARN: arn:aws:bedrock-agentcore:us-west-2:************:harness/harness_work-**********
  Description: Managed AgentCore Harness for the harness-work project. Invoke via bedrock-agentcore InvokeHarness (streaming). Default tools: exa, aws_knowledge, browser, code
[3/3] Invoking Harness
--- InvokeHarness stream ---

[messageStart]
안녕하세요! 저는 서연이에요 😊 AWS와 AI 기술, 문서 작성, 시스템 탐색 등 다양한 작업을 도와드리는 대화형 AI입니다.

**사용 가능한 도구 한 줄 요약:** 셸/파일 편집, 웹 검색·페이지 읽기, AWS 공식 문서 검색·리전 조회, 브라우저 자동화,코드 인터프리터(Python/JS/TS), 지식베이스 검색(retrieve), 그리고 산출물 공유(share_artifact) 도구를 사용할 수 있어요!
[contentBlockStop]


[Stop reason: end_turn]

[Tokens - input: 24048, output: 220]

============================================================
InvokeHarness completed (231 chars)
============================================================
```

이 예에서:

- **Discovery**: AGENT `harness-work`와 MCP `knowledge-base`가 함께 검색되고, AGENT를 우선 선택
- **Connection**: Agent Card에서 Harness ARN 추출
- **Execution**: `InvokeHarness` 스트리밍 응답·토큰 usage까지 정상

### 설정 참조 (`application/config.json`)

테스트가 읽는 주요 키:

| 키 | 용도 |
|----|------|
| `registry_id` | 검색 대상 Registry |
| `region` | API / Invoke 리전 |
| `kb_mcp_url` | MCP 테스트: 레코드에서 URL을 못 읽을 때 fallback |
| `kb_mcp_runtime_arn` | MCP 테스트: Runtime resource policy 갱신 대상 |
| `kb_knowledge_base_id` | MCP 테스트: empty 결과 시 Bedrock fallback retrieve |
| `harness_agent_harness_arn` | Agent 테스트: 레코드에서 ARN을 못 읽을 때 fallback |
| `harness_agent_record_name` | 등록된 AGENT 레코드 이름 (`harness-work`) |

### 관련 파일

| 파일 | 역할 |
|------|------|
| `src/test_registry_mcp.py` | KB MCP Discovery → retrieve E2E |
| `src/test_registry_agent.py` | Harness AGENT Discovery → InvokeHarness E2E |
| `src/register_mcp.py` | KB MCP 레코드를 Registry에 등록 |
| `src/register_agent.py` | harness_work AGENT 레코드를 Registry에 등록 |
| `src/registry.py` | Registry 생성 |
| `application/config.json` | 배포/등록 결과 |
| `../harness-work/application/config.json` | Harness ARN / harnessId 소스 |

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

| 항목 | Preview (`bedrock-agentcore`) | GA (`agent-registry`) |
|------|-------------------------------|------------------------|
| 네임스페이스 | `bedrock-agentcore` | `agent-registry` |
| Control plane SDK | `bedrock-agentcore-control` | `agent-registry-control` |
| Data plane SDK | `bedrock-agentcore` | `agent-registry` |
| Control endpoint | `bedrock-agentcore-control.{region}.amazonaws.com` | `agent-registry-control.{region}.api.aws` |
| Data / MCP endpoint | `bedrock-agentcore.{region}.amazonaws.com` | `agent-registry.{region}.api.aws` |
| CLI | `aws bedrock-agentcore-control` | `aws agent-registry-control` |
| boto3 최소 버전 | ≥ 1.42.87 (preview API) | **≥ 1.43.84** |
| **지원 종료일** | **2026년 9월 17일** | — |

> Preview에서 만든 Registry는 GA 네임스페이스에 자동으로 나타나지 않습니다. GA로 전환하려면 `python3 src/registry.py`로 새 Registry를 생성하거나 [마이그레이션 가이드](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-faq.html)를 따르세요.

---

## Reference

1. [AWS Agent Registry 개요](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry.html)
2. [Key capabilities](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-key-capabilities.html)
3. [Concepts and terminology](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-concepts.html)
4. [Registry Get Started](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-get-started.html)
5. [Amazon Bedrock AgentCore Pricing (Registry 포함)](https://aws.amazon.com/bedrock/agentcore/pricing/)
6. [Manage agents, tools and skills at scale with AWS Agent Registry (Blog)](https://aws.amazon.com/blogs/machine-learning/manage-agents-tools-and-skills-at-scale-with-aws-agent-registry/)
7. [Comprehensive registry migration guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-faq.html)
8. [AWS Agent Registry Release Notes](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/release-notes.html)
9. [CreateRegistry API](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CreateRegistry.html)
10. [Agentic AI Lens — Agent Registry BP](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentrel04-bp02.html)
11. [AgentCore Gateway](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentops04-bp01.html)
12. [AgentCore Gateway Target](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec02-bp03.html)
