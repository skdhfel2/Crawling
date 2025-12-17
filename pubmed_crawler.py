"""
PubMed/PMC PDF Crawler
======================
PubMed에서 논문을 검색하고 PMC에서 PDF를 다운로드하는 크롤러

사용법:
    python pubmed_crawler.py --query "검색어" --max_results 100

주의사항:
    - API 키 없이는 초당 3회 요청 제한
    - Open Access 논문만 PDF 다운로드 가능
    - NCBI 이용 약관을 준수해주세요
"""

import requests
import os
import sys
import io
import time
import argparse
import json
import re
import tarfile
import tempfile
import shutil
from urllib.parse import urljoin
from datetime import datetime
from typing import Optional, List, Dict, Tuple
from bs4 import BeautifulSoup
from xml.etree import ElementTree as ET

# Windows 콘솔 UTF-8 인코딩 설정
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


def load_env_file():
    """현재 디렉토리의 .env 파일에서 환경변수 로드"""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key.strip(), value.strip())


# .env 파일 로드
load_env_file()


class PubMedCrawler:
    """PubMed/PMC에서 논문 PDF를 크롤링하는 클래스"""
    
    # NCBI API 엔드포인트
    ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    ELINK_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"
    PMC_OA_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"
    
    def __init__(self, output_dir: str = "downloads", api_key: Optional[str] = None):
        """
        크롤러 초기화
        
        Args:
            output_dir: PDF 저장 디렉토리
            api_key: NCBI API 키 (선택사항, 없으면 환경변수 NCBI_API_KEY 사용)
        """
        self.output_dir = output_dir
        # API 키: 인자 > 환경변수 > None 순으로 확인
        self.api_key = api_key if api_key else os.environ.get('NCBI_API_KEY')
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'PubMedCrawler/1.0 (Academic Research Purpose; Contact: researcher@example.com)'
        })
        
        # API 키가 있으면 초당 10회
        self.request_delay = 0.34 if self.api_key else 0.5
        
        # 출력 디렉토리 생성
        os.makedirs(output_dir, exist_ok=True)
        
        # 로그 파일
        self.log_file = os.path.join(output_dir, "crawl_log.json")
        self.results_log = []
        
    def _make_request(self, url: str, params: Dict = None, retry_count: int = 3) -> Optional[requests.Response]:
        """API 요청 수행 (재시도 로직 포함)"""
        if params is None:
            params = {}
        if self.api_key:
            params['api_key'] = self.api_key
            
        for attempt in range(retry_count):
            try:
                time.sleep(self.request_delay)
                response = self.session.get(url, params=params, timeout=30)
                response.raise_for_status()
                return response
            except requests.exceptions.RequestException as e:
                print(f"  [!] Request failed (attempt {attempt + 1}/{retry_count}): {e}")
                if attempt < retry_count - 1:
                    time.sleep(2 ** attempt)
        return None
    
    def search_pmc(self, query: str, max_results: int = 100, 
                    start_date: Optional[str] = None, 
                    end_date: Optional[str] = None) -> List[str]:
        """PMC 데이터베이스에서 직접 검색 (Open Access 논문만)"""
        print(f"\n[*] Searching PMC (Open Access): '{query}'")
        
        params = {
            'db': 'pmc',
            'term': query,
            'retmax': max_results,
            'retmode': 'json',
            'usehistory': 'y'
        }
        
        if start_date and end_date:
            params['datetype'] = 'pdat'
            params['mindate'] = start_date
            params['maxdate'] = end_date
        
        response = self._make_request(self.ESEARCH_URL, params)
        
        if not response:
            print("[X] Search failed")
            return []
            
        data = response.json()
        
        if 'esearchresult' not in data:
            print("[X] Failed to parse search results")
            return []
            
        result = data['esearchresult']
        total_count = int(result.get('count', 0))
        id_list = result.get('idlist', [])
        
        # PMC ID 형식으로 변환
        pmc_ids = [f"PMC{id}" for id in id_list]
        
        print(f"    Found {total_count:,} total results, fetched {len(pmc_ids)}")
        
        return pmc_ids
    
    def get_pmc_ids(self, pubmed_ids: List[str]) -> Dict[str, str]:
        """PubMed ID를 PMC ID로 변환"""
        print(f"\n[*] Looking up PMC IDs ({len(pubmed_ids)} articles)...")
        
        pmc_map = {}
        batch_size = 200
        
        for i in range(0, len(pubmed_ids), batch_size):
            batch = pubmed_ids[i:i + batch_size]
            
            params = {
                'dbfrom': 'pubmed',
                'db': 'pmc',
                'id': ','.join(batch),
                'retmode': 'json'
            }
            
            response = self._make_request(self.ELINK_URL, params)
            
            if not response:
                continue
                
            try:
                data = response.json()
                linksets = data.get('linksets', [])
                
                for linkset in linksets:
                    pmid = str(linkset.get('ids', [None])[0])
                    linksetdbs = linkset.get('linksetdbs', [])
                    
                    for linksetdb in linksetdbs:
                        if linksetdb.get('dbto') == 'pmc':
                            links = linksetdb.get('links', [])
                            if links:
                                pmc_id = f"PMC{links[0]}"
                                pmc_map[pmid] = pmc_id
            except Exception as e:
                print(f"  [!] PMC ID parsing error: {e}")
                
        print(f"    {len(pmc_map)} articles available in PMC (Open Access)")
        
        return pmc_map
    
    def get_article_info(self, pubmed_ids: List[str]) -> Dict[str, Dict]:
        """논문 메타데이터 가져오기"""
        print(f"\n[*] Fetching article metadata...")
        
        articles = {}
        batch_size = 100
        
        for i in range(0, len(pubmed_ids), batch_size):
            batch = pubmed_ids[i:i + batch_size]
            
            params = {
                'db': 'pubmed',
                'id': ','.join(batch),
                'retmode': 'xml'
            }
            
            response = self._make_request(self.EFETCH_URL, params)
            
            if not response:
                continue
                
            try:
                root = ET.fromstring(response.content)
                
                for article in root.findall('.//PubmedArticle'):
                    pmid_elem = article.find('.//PMID')
                    if pmid_elem is None:
                        continue
                        
                    pmid = pmid_elem.text
                    
                    title_elem = article.find('.//ArticleTitle')
                    title = title_elem.text if title_elem is not None and title_elem.text else "Unknown"
                    
                    authors = []
                    for author in article.findall('.//Author'):
                        lastname = author.find('LastName')
                        forename = author.find('ForeName')
                        if lastname is not None and lastname.text:
                            name = lastname.text
                            if forename is not None and forename.text:
                                name = f"{forename.text} {name}"
                            authors.append(name)
                    
                    journal_elem = article.find('.//Journal/Title')
                    journal = journal_elem.text if journal_elem is not None and journal_elem.text else "Unknown"
                    
                    year_elem = article.find('.//PubDate/Year')
                    year = year_elem.text if year_elem is not None and year_elem.text else "Unknown"
                    
                    doi = None
                    for aid in article.findall('.//ArticleId'):
                        if aid.get('IdType') == 'doi':
                            doi = aid.text
                            break
                    
                    articles[pmid] = {
                        'pmid': pmid,
                        'title': title,
                        'authors': authors,
                        'journal': journal,
                        'year': year,
                        'doi': doi
                    }
                    
            except Exception as e:
                print(f"  [!] Metadata parsing error: {e}")
                
        print(f"    Collected info for {len(articles)} articles")
        
        return articles
    
    def get_oa_download_link(self, pmc_id: str) -> Optional[Tuple[str, str]]:
        """
        NCBI OA Service를 통해 다운로드 링크 가져오기
        
        Returns:
            (url, format) 튜플 또는 None
        """
        params = {'id': pmc_id}
        response = self._make_request(self.PMC_OA_URL, params)
        
        if not response:
            return None
            
        try:
            root = ET.fromstring(response.content)
            
            # 에러 체크
            error = root.find('.//error')
            if error is not None:
                return None
            
            # 다운로드 링크 찾기 (PDF 우선, 없으면 tgz)
            record = root.find('.//record')
            if record is None:
                return None
                
            links = record.findall('.//link')
            
            pdf_link = None
            tgz_link = None
            
            for link in links:
                fmt = link.get('format', '')
                href = link.get('href', '')
                
                if fmt == 'pdf':
                    pdf_link = href
                elif fmt == 'tgz':
                    tgz_link = href
            
            if pdf_link:
                return (pdf_link, 'pdf')
            elif tgz_link:
                return (tgz_link, 'tgz')
                
        except Exception as e:
            pass
            
        return None
    
    def download_from_ftp(self, ftp_url: str, local_path: str) -> bool:
        """FTP URL에서 파일 다운로드"""
        try:
            # FTP URL을 HTTP로 변환 (NCBI FTP는 HTTP로도 접근 가능)
            if ftp_url.startswith('ftp://'):
                http_url = ftp_url.replace('ftp://ftp.ncbi.nlm.nih.gov', 
                                           'https://ftp.ncbi.nlm.nih.gov')
            else:
                http_url = ftp_url
            
            time.sleep(self.request_delay)
            response = self.session.get(http_url, timeout=120, stream=True)
            response.raise_for_status()
            
            with open(local_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    
            return os.path.getsize(local_path) > 1024
            
        except Exception as e:
            if os.path.exists(local_path):
                os.remove(local_path)
            return False
    
    def extract_pdf_from_tgz(self, tgz_path: str, output_path: str) -> bool:
        """tar.gz 파일에서 PDF 추출"""
        try:
            with tarfile.open(tgz_path, 'r:gz') as tar:
                for member in tar.getmembers():
                    if member.name.lower().endswith('.pdf'):
                        # PDF 파일 추출
                        pdf_file = tar.extractfile(member)
                        if pdf_file:
                            with open(output_path, 'wb') as f:
                                f.write(pdf_file.read())
                            return True
            return False
        except Exception as e:
            return False
    
    def download_article(self, pmc_id: str, filename: str) -> Tuple[bool, str]:
        """
        논문 다운로드 (OA Service 사용)
        
        Returns:
            (성공 여부, 상태 메시지)
        """
        filepath = os.path.join(self.output_dir, filename)
        
        if os.path.exists(filepath):
            return (True, 'already_exists')
        
        # OA 서비스에서 다운로드 링크 가져오기
        link_info = self.get_oa_download_link(pmc_id)
        
        if not link_info:
            return (False, 'no_oa_link')
            
        url, fmt = link_info
        
        if fmt == 'pdf':
            # 직접 PDF 다운로드
            if self.download_from_ftp(url, filepath):
                return (True, 'direct_pdf')
            else:
                return (False, 'download_failed')
                
        elif fmt == 'tgz':
            # tar.gz 다운로드 후 PDF 추출
            with tempfile.TemporaryDirectory() as tmpdir:
                tgz_path = os.path.join(tmpdir, f"{pmc_id}.tar.gz")
                
                if not self.download_from_ftp(url, tgz_path):
                    return (False, 'tgz_download_failed')
                
                if self.extract_pdf_from_tgz(tgz_path, filepath):
                    return (True, 'extracted_from_tgz')
                else:
                    return (False, 'pdf_extraction_failed')
        
        return (False, 'unknown_format')
    
    def sanitize_filename(self, name: str, max_length: int = 100) -> str:
        """파일명에서 특수문자 제거"""
        if not name:
            name = "Unknown"
        name = re.sub(r'[<>:"/\\|?*]', '_', name)
        name = re.sub(r'[\s_]+', '_', name)
        if len(name) > max_length:
            name = name[:max_length]
        return name.strip('_')
    
    def get_pmc_article_info(self, pmc_ids: List[str]) -> Dict[str, Dict]:
        """PMC 논문 메타데이터 가져오기"""
        print(f"\n[*] Fetching PMC article metadata...")
        
        articles = {}
        batch_size = 50  # 배치 크기 줄임
        
        # PMC ID에서 숫자만 추출
        numeric_ids = [pmc_id.replace('PMC', '') for pmc_id in pmc_ids]
        
        for i in range(0, len(numeric_ids), batch_size):
            batch = numeric_ids[i:i + batch_size]
            batch_pmc_ids = pmc_ids[i:i + batch_size]
            
            params = {
                'db': 'pmc',
                'id': ','.join(batch),
                'retmode': 'xml'
            }
            
            response = self._make_request(self.EFETCH_URL, params)
            
            if not response:
                continue
                
            try:
                root = ET.fromstring(response.content)
                article_elements = root.findall('.//article')
                
                # 순서대로 매칭 (API가 요청 순서대로 반환)
                for idx, article in enumerate(article_elements):
                    if idx >= len(batch_pmc_ids):
                        break
                    
                    pmc_id = batch_pmc_ids[idx]
                    
                    # 제목 (여러 경로 시도)
                    title = "Unknown"
                    for title_path in ['.//article-title', './/title-group/article-title', './/front/article-meta/title-group/article-title']:
                        title_elem = article.find(title_path)
                        if title_elem is not None:
                            title = "".join(title_elem.itertext()).strip()
                            if title:
                                break
                    
                    # 저자
                    authors = []
                    for contrib in article.findall('.//contrib[@contrib-type="author"]'):
                        surname = contrib.find('.//surname')
                        given = contrib.find('.//given-names')
                        if surname is not None and surname.text:
                            name = surname.text
                            if given is not None and given.text:
                                name = f"{given.text} {name}"
                            authors.append(name)
                    
                    # 저널
                    journal = "Unknown"
                    for journal_path in ['.//journal-title', './/journal-meta/journal-title']:
                        journal_elem = article.find(journal_path)
                        if journal_elem is not None and journal_elem.text:
                            journal = journal_elem.text.strip()
                            break
                    
                    # 출판 연도 (여러 경로 시도)
                    year = ""
                    for year_path in ['.//pub-date/year', './/pub-date[@pub-type="epub"]/year', './/pub-date[@date-type="pub"]/year']:
                        year_elem = article.find(year_path)
                        if year_elem is not None and year_elem.text:
                            year = year_elem.text.strip()
                            break
                    
                    articles[pmc_id] = {
                        'pmc_id': pmc_id,
                        'title': title,
                        'authors': authors,
                        'journal': journal,
                        'year': year
                    }
                    
            except Exception as e:
                print(f"  [!] Metadata parsing error: {e}")
                
        print(f"    Collected info for {len(articles)} articles")
        
        return articles
    
    def crawl(self, query: str, max_results: int = 100,
              start_date: Optional[str] = None,
              end_date: Optional[str] = None) -> Dict:
        """크롤링 실행"""
        start_time = datetime.now()
        print("="*60)
        print("  PMC PDF Crawler (Open Access)")
        print("="*60)
        
        # 1. PMC 직접 검색
        pmc_ids = self.search_pmc(query, max_results, start_date, end_date)
        
        if not pmc_ids:
            return {'status': 'error', 'message': 'No search results'}
        
        # 2. 논문 정보 가져오기
        articles = self.get_pmc_article_info(pmc_ids)
        
        # 3. PDF 다운로드
        print(f"\n[*] Downloading PDFs ({len(pmc_ids)} articles)...")
        
        success_count = 0
        fail_count = 0
        
        for i, pmc_id in enumerate(pmc_ids, 1):
            article_info = articles.get(pmc_id, {})
            title = article_info.get('title', 'Unknown')
            year = article_info.get('year', '')
            
            # 진행 상황 출력
            short_title = title[:40] + "..." if len(title) > 40 else title
            print(f"\n  [{i}/{len(pmc_ids)}] {short_title}")
            
            # 파일명 생성
            safe_title = self.sanitize_filename(title)
            filename = f"{pmc_id}_{year}_{safe_title}.pdf"
            
            # 다운로드
            success, status = self.download_article(pmc_id, filename)
            
            if success:
                print(f"      [OK] Downloaded: {filename[:50]}...")
                success_count += 1
                self.results_log.append({
                    'pmc_id': pmc_id,
                    'title': title,
                    'filename': filename,
                    'status': status
                })
            else:
                print(f"      [X] Failed: {status}")
                fail_count += 1
                self.results_log.append({
                    'pmc_id': pmc_id,
                    'title': title,
                    'status': status
                })
        
        # 4. 결과 저장
        elapsed_time = (datetime.now() - start_time).total_seconds()
        
        result_summary = {
            'query': query,
            'total_found': len(pmc_ids),
            'downloaded': success_count,
            'failed': fail_count,
            'elapsed_time': f"{elapsed_time:.1f}s",
            'output_directory': os.path.abspath(self.output_dir),
            'timestamp': datetime.now().isoformat(),
            'details': self.results_log
        }
        
        # 로그 파일 저장
        with open(self.log_file, 'w', encoding='utf-8') as f:
            json.dump(result_summary, f, ensure_ascii=False, indent=2)
        
        # 결과 출력
        print("\n" + "="*60)
        print("  Crawling Complete!")
        print("="*60)
        print(f"  Query: {query}")
        print(f"  Total found: {len(pmc_ids)}")
        print(f"  Downloaded: {success_count}")
        print(f"  Failed: {fail_count}")
        print(f"  Time: {elapsed_time:.1f}s")
        print(f"  Output: {os.path.abspath(self.output_dir)}")
        print("="*60)
        
        return result_summary


def main():
    parser = argparse.ArgumentParser(
        description='PubMed/PMC PDF Crawler',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python pubmed_crawler.py --query "machine learning"
  python pubmed_crawler.py --query "COVID-19" --max_results 50
  python pubmed_crawler.py --query "cancer" --start_date 2023/01/01 --end_date 2024/01/01
        """
    )
    
    parser.add_argument('--query', '-q', type=str, required=True,
                       help='Search query (required)')
    parser.add_argument('--max_results', '-m', type=int, default=100,
                       help='Maximum number of results (default: 100)')
    parser.add_argument('--output', '-o', type=str, default='downloads',
                       help='Output directory (default: downloads)')
    parser.add_argument('--api_key', '-k', type=str, default=None,
                       help='NCBI API key (optional, for faster requests)')
    parser.add_argument('--start_date', type=str, default=None,
                       help='Start date (YYYY/MM/DD format)')
    parser.add_argument('--end_date', type=str, default=None,
                       help='End date (YYYY/MM/DD format)')
    
    args = parser.parse_args()
    
    crawler = PubMedCrawler(
        output_dir=args.output,
        api_key=args.api_key
    )
    
    crawler.crawl(
        query=args.query,
        max_results=args.max_results,
        start_date=args.start_date,
        end_date=args.end_date
    )


if __name__ == '__main__':
    main()
