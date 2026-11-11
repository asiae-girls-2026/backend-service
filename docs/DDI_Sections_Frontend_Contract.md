# DDI 결과 카드 연동 — QA7

목표: 긴 답변의 ‘자료에 적힌 내용:’을 화면에 반복하지 않고, 기존 복용법/주의 카드와 같은 색상 라벨로 구분한다. 근거 본문은 ‘확인된 내용’, 검사 한계와 후속 확인 안내는 ‘주의’에 표시한다.

## 응답 계약

기존 `POST /v1/medication-chat/confirm` 응답에 필드 2개를 추가한다. 새 엔드포인트는 없다.

```ts
export type DdiSection = {
  type: 'EVIDENCE' | 'RESULT' | 'CAUTION'
  title: string
  items: string[]
  product_ids?: number[]
  product_names?: string[]
}

// 기존 MedChatReply에 추가
answer?: string
presentation_version?: string
ddi_sections_version?: number | null
ddi_sections?: DdiSection[]
```

- `ddi_sections_version: 1`, `presentation_version: '20261111.7'`.
- `message`, `answer`, `status`, 판정·근거 필드는 유지한다.
- `ddi_sections` 배열은 서버 순서대로 표시한다. 제품쌍별 `EVIDENCE` 또는 `RESULT`가 먼저 나오고, 공통 `CAUTION`은 마지막에 한 번 나온다.
- 제품쌍이 여러 개면 각 영역의 `product_names`로 제품쌍을 함께 표시한다. 서로 다른 조합의 근거를 섞지 않는다.
- 이 필드는 DDI 결과에만 추가된다. 일반 등록·조회·오류 응답에는 없어도 정상이다.

```json
{
  "status": "COMPLETED",
  "intent": "DDI_CHECK",
  "message": "기존 일반 답변",
  "answer": "기존 일반 답변",
  "presentation_version": "20261111.7",
  "ddi_sections_version": 1,
  "ddi_sections": [
    {
      "type": "EVIDENCE",
      "title": "확인된 내용",
      "product_ids": [1, 2],
      "product_names": ["테스트약A", "테스트약B"],
      "items": ["확인된 근거의 원문 전체"]
    },
    {
      "type": "CAUTION",
      "title": "주의",
      "items": [
        "이 결과만으로 개인의 복용 가능 여부를 결정할 수는 없어요.",
        "일부 상호작용은 확인하지 못했어요."
      ]
    }
  ]
}
```

위 제품명·ID·근거는 응답 형태를 설명하는 예시이며 실제 임상 정보가 아니다.

## 표시 규칙

| type | 라벨 | 표현 |
|---|---|---|
| EVIDENCE | 확인된 내용 | 기존 복용법 라벨과 같은 민트/초록 배지. 제품명 아래 `items` 본문 표시 |
| RESULT | 검사 결과 | 민트/초록 배지. 정보 부족·미확인 결과도 이 영역에 표시. ‘안전’ 판정으로 바꾸지 않음 |
| CAUTION | 주의 | 기존 주의 라벨과 같은 코랄/빨간 배지. 경고 아이콘을 붙여도 됨 |

- 라벨은 문장 앞에 ‘자료에 적힌 내용:’처럼 붙이지 않고 배지 요소로 표시한다.
- `items`에 포함된 근거·위험 설명·검사 한계는 생략하거나 문장 중간을 자르지 않는다. 길면 카드가 세로로 늘어나거나 전체 내용을 펼칠 수 있게 한다.
- 카드 표시 시 기존 `message`/`answer`를 동시에 출력하지 않는다. 같은 내용이 두 번 보이지 않게 한다.
- `ddi_sections_version !== 1`, 필드 누락, 빈 배열, 알 수 없는/잘못된 section이 하나라도 있으면 전체 카드 대신 기존 `message || answer`를 표시한다. 잘못된 항목만 제거해서 주의가 사라지는 상태를 만들지 않는다.
- 사이트는 본문에 포함된 ‘건강보험심사평가원 DUR 국민 체험관’ 이름만 표시한다. 외부 링크 버튼은 추가하지 않는다.
- `assessment_status`, `coverage`로 프론트가 자체 안전 판정을 만들지 않는다.

## 기존 코드에서 연결할 위치

검토한 carepong-app 소스 기준이며 담당자 작업본의 줄 번호는 달라질 수 있다.

1. `app/src/main/api.ts`: API 응답 타입에서 신규 필드를 유지한다. API 요청 함수는 기존 confirm을 사용한다.
2. `app/src/main/index.ts`: `medCall()`의 성공 응답 `data`를 그대로 전달한다. 새 필드가 유실되지 않도록 한다.
3. `app/src/preload/index.ts`, `index.d.ts`와 renderer의 중복 `MedChatReply` 선언에 타입을 추가한다.
4. `app/src/renderer/ChatApp.tsx`: `ChatMessage`에 `ddiSections?: DdiSection[]` 추가. `renderLuiReply()`에서 유효한 sections를 메시지에 저장한다.
5. 말풍선 렌더링: sections가 있으면 DDI 카드, 없으면 기존 일반 답변을 표시한다.
6. 기존 `DrugCard`와 `drug-card__tag`, `drug-card__tag--warn` 등의 스타일을 재사용한다. 백엔드 답변을 정규식으로 잘라 카드로 만들지 않는다.

간단한 카드 컴포넌트 예시:

```tsx
function DdiCard({ sections }: { sections: DdiSection[] }) {
  return (
    <div className="drug-card">
      {sections.map((section, index) => (
        <section key={index} className="ddi-section">
          {section.product_names?.length ? (
            <p className="drug-card__name">{section.product_names.join(' · ')}</p>
          ) : null}
          <span className={
            'drug-card__tag' +
            (section.type === 'CAUTION' ? ' drug-card__tag--warn' : '')
          }>
            {section.type === 'CAUTION' ? <span aria-hidden="true">⚠ </span> : null}
            {section.title}
          </span>
          <ul>
            {section.items.map((item, i) => <li key={i}>{item}</li>)}
          </ul>
        </section>
      ))}
    </div>
  )
}
```

실제 적용 시 기존 말풍선 구조에 맞춰 여백과 줄 간격만 조정한다. React 텍스트 렌더링을 사용하고 서버 문자열을 HTML로 삽입하지 않는다.

## 완료 확인

- 승인 근거가 있는 조합: ‘확인된 내용’ 배지와 원문, 마지막 ‘주의’ 배지.
- 정보 부족·참고자료만 있는 조합: ‘검사 결과’와 ‘주의’; 미검증 근거를 확정된 위험 정보로 표시하지 않음.
- 여러 제품: 각 제품쌍을 구분하고 공통 주의는 마지막 한 번.
- 일반 답변과 카드가 중복되지 않음.
- 이전 서버 응답/잘못된 새 필드: 기존 일반 답변으로 정상 표시.
- 등록·시간·요일 선택·취소·오류 처리가 기존대로 동작.
- 실제 사용자 토큰으로 앱 → FastAPI → Lambda → 앱 결과 표시를 확인.

이 작업은 DDI 결과 표시만 다룬다. 기존 전달 목록의 DDI 버튼 문구·오류 보존·등록약 선택·관리 화면 연결은 별도 작업으로 계속 필요하다.

## 배포 순서

백엔드 패치 → Lambda 배포 → FastAPI 배포 → 프론트 연결 → Electron 새 빌드·배포.
기존 앱에는 일반 답변이 계속 표시된다. 색상 카드 표시를 보려면 프론트가 수정된 앱 버전을 설치해야 한다.
