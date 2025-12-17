# PubMed/PMC PDF 크롤러

PubMed에서 논문을 검색하고 PMC(PubMed Central)에서 PDF를 다운로드하는 Python 크롤러입니다.

## 🚀 설치

```bash
pip install -r requirements.txt
```

## 📖 사용법

### 기본 사용
```bash
python pubmed_crawler.py --query "검색어"
```

### 전체 옵션
```bash
python pubmed_crawler.py --query "machine learning" --max_results 50 --output ./papers
```

| 옵션 | 단축 | 설명 | 기본값 |
|------|------|------|--------|
| `--query` | `-q` | 검색어 (필수) | - |
| `--max_results` | `-m` | 최대 결과 수 | 100 |
| `--output` | `-o` | 저장 디렉토리 | downloads |
| `--api_key` | `-k` | NCBI API 키 | None |
| `--start_date` | - | 시작 날짜 (YYYY/MM/DD) | None |
| `--end_date` | - | 종료 날짜 (YYYY/MM/DD) | None |

### 예제

```bash
# 기본 검색
python pubmed_crawler.py --query "COVID-19"

# 최대 50개만 다운로드
python pubmed_crawler.py --query "cancer treatment" --max_results 50

# 날짜 범위 지정
python pubmed_crawler.py --query "diabetes" --start_date 2023/01/01 --end_date 2024/01/01

# 특정 폴더에 저장
python pubmed_crawler.py --query "machine learning healthcare" --output ./ml_papers

# API 키 사용 (속도 향상)
python pubmed_crawler.py --query "genomics" --api_key YOUR_API_KEY
```

## 📁 출력 구조

```
downloads/
├── PMC9308575_2022_논문제목.pdf
├── PMC8765432_2023_다른논문.pdf
├── ...
└── crawl_log.json  # 크롤링 로그
```

## ⚠️ 주의사항

1. **Rate Limiting**: API 키 없이는 초당 3회 요청으로 제한됩니다
2. **Open Access만 가능**: PMC에 있는 Open Access 논문만 PDF 다운로드 가능
3. **이용 약관 준수**: NCBI 이용 약관을 반드시 준수해주세요
4. **대량 다운로드 주의**: 너무 많은 논문을 한 번에 다운로드하면 IP가 차단될 수 있습니다

## 🔑 API 키 발급

더 빠른 크롤링을 원한다면 NCBI API 키를 무료로 발급받을 수 있습니다:
1. https://www.ncbi.nlm.nih.gov/account/ 에서 계정 생성
2. Settings → API Key Management에서 키 발급

## 📊 크롤링 로그

`crawl_log.json` 파일에 크롤링 결과가 저장됩니다:

```json
{
  "query": "machine learning",
  "total_found": 1000,
  "pmc_available": 150,
  "downloaded": 142,
  "failed": 8,
  "elapsed_time": "245.3초",
  "details": [...]
}
```

## 🐍 Python 코드에서 사용

```python
from pubmed_crawler import PubMedCrawler

crawler = PubMedCrawler(output_dir="my_papers")
result = crawler.crawl(
    query="artificial intelligence in medicine",
    max_results=50
)

print(f"다운로드 완료: {result['downloaded']}개")
```
