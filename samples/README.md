# 샘플 PDF 소스 정리

## 1. 공식 참고 문서

다음 링크들은 실제 전자세금계산서 제도, 발급 흐름, 수정 발급 방식을 이해하기 위한 참고 자료입니다.

1. 국세청 전자세금계산서 발급방법 및 발급절차  
   https://b.nts.go.kr/nts/cm/cntnts/cntntsView.do?cntntsId=7788&mi=2462
2. 홈택스 수정전자세금계산서 발급 안내 PDF  
   https://teet.hometax.go.kr/doc/et/b/a/b/%EC%A1%B0%ED%9A%8C%EB%B0%9C%EA%B8%89_%EC%A0%84%EC%9E%90%EC%84%B8%EA%B8%88%EA%B3%84%EC%82%B0%EC%84%9C_%EC%88%98%EC%A0%95%EC%A0%84%EC%9E%90%EC%84%B8%EA%B8%88%EA%B3%84%EC%82%B0%EC%84%9C.pdf

## 2. 공개 양식 참고 소스

공개 양식 사이트는 레이아웃 다양성 확보용으로만 사용하고, 실제 업무 기준 검증은 반드시 비식별 실문서로 추가 보정해야 합니다.

1. 공폼 무료양식 메인  
   https://gongform.com/
2. 공폼 세금계산서 양식 관련 검색 진입점  
   https://gongform.com/

## 3. 이 프로젝트의 샘플 전략

공개 PDF만으로는 실제 세무사무소 문서 편차를 충분히 반영하기 어렵기 때문에, 아래 방식으로 샘플 세트를 구성합니다.

1. 공식 문서로 필드 구조 및 용어 확인
2. 공개 양식으로 기본 레이아웃 패턴 확인
3. 프로젝트 내 `sample_cases.json` 기준으로 테스트용 PDF 10건 생성
4. 이후 실제 비식별 샘플 2~3건을 받아 추출기 보정

## 4. 생성 방법

```powershell
cd C:\Project\데모\doc-auto-mvp
python scripts\generate_sample_pdfs.py
```

생성 결과:
1. `samples\generated\*.pdf`
2. `samples\generated\expected_fields.json`
