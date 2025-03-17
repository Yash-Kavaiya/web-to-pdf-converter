import os
import time
import requests
import logging
from flask import Flask, render_template, request, send_from_directory, url_for, redirect, flash, jsonify
from bs4 import BeautifulSoup
import pdfkit
from urllib.parse import urljoin, urlparse
import uuid
import threading
import queue
import sys
import PyPDF2  # For merging PDFs

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Replace with a real secret key in production

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('webapp.log')
    ]
)
logger = logging.getLogger(__name__)

# Create necessary directories
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pdfs')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Dictionary to store job status
job_status = {}
url_queue = queue.Queue()

def get_filename_from_url(url):
    """Generate a filename from a URL."""
    parsed = urlparse(url)
    path = parsed.netloc + parsed.path
    if path.endswith('/'):
        path = path[:-1]
    clean_name = ''.join(c if c.isalnum() or c in '._- ' else '_' for c in path)
    return clean_name[:100]  # Limit filename length

def url_to_pdf(url, output_path):
    """Convert a URL to PDF."""
    try:
        # Configure pdfkit options
        options = {
            'page-size': 'A4',
            'margin-top': '0.75in',
            'margin-right': '0.75in',
            'margin-bottom': '0.75in',
            'margin-left': '0.75in',
            'encoding': 'UTF-8',
            'no-outline': None,
            'quiet': '',
            'javascript-delay': 2000,      # Wait for JS to execute (2 seconds)
            'no-stop-slow-scripts': True,  # Don't stop slow running scripts
            'enable-local-file-access': True, # Allow access to local files
            'load-error-handling': 'ignore', # Continue on page load errors
            'custom-header': [
                ('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.0.0 Safari/537.36')
            ]
        }
        
        # Specify path to wkhtmltopdf executable - update this path to match your system
        config = pdfkit.configuration(wkhtmltopdf='/usr/bin/wkhtmltopdf')  # Linux path
        # For Windows, use something like: config = pdfkit.configuration(wkhtmltopdf='C:\\Program Files\\wkhtmltopdf\\bin\\wkhtmltopdf.exe')
        # For macOS with homebrew: config = pdfkit.configuration(wkhtmltopdf='/usr/local/bin/wkhtmltopdf')
        
        # First try to get the page content
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching {url}: {e}")
            return False
        
        # Convert URL to PDF using configuration
        pdfkit.from_url(url, output_path, options=options, configuration=config)
        
        # Verify the PDF was created and has content
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return True
        else:
            print(f"PDF was created but is empty for {url}")
            return False
    except Exception as e:
        print(f"Error converting {url} to PDF: {e}")
        return False

def extract_urls(url):
    """Extract all URLs from a webpage."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.0.0 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()  # Check for HTTP errors
        
        # Check content type
        content_type = response.headers.get('Content-Type', '').lower()
        if 'text/html' not in content_type:
            logger.warning(f"URL {url} is not HTML (Content-Type: {content_type})")
            return []
        
        soup = BeautifulSoup(response.text, 'html.parser')
        base_url = "{0.scheme}://{0.netloc}".format(urlparse(url))
        path_base = urlparse(url).path
        
        links = []
        for link in soup.find_all('a', href=True):
            href = link['href'].strip()
            
            # Skip anchors, javascript, mailto, etc.
            if (not href or href == '/' or href.startswith('#') or 
                href.startswith('javascript:') or href.startswith('mailto:') or
                href.startswith('tel:')):
                continue
                
            # Handle relative URLs
            if not href.startswith(('http://', 'https://')):
                href = urljoin(base_url, href)
            
            # Skip external links (optional, remove if you want all links)
            if urlparse(base_url).netloc != urlparse(href).netloc:
                continue
            
            # Skip URLs with fragments
            href = href.split('#')[0]
            
            # Skip common file types that are not web pages
            if any(href.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.pdf', '.zip', '.doc', '.docx']):
                continue
                
            links.append(href)
            
        # Remove duplicates while preserving order
        unique_links = []
        for link in links:
            if link not in unique_links:
                unique_links.append(link)
        
        logger.info(f"Extracted {len(unique_links)} unique URLs from {url}")
        return unique_links
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error extracting URLs from {url}: {e}")
        return []
    except Exception as e:
        logger.error(f"Error extracting URLs from {url}: {e}", exc_info=True)
        return []

def merge_pdfs(pdf_paths, output_path):
    """Merge multiple PDFs into a single PDF file."""
    try:
        logger.info(f"Merging {len(pdf_paths)} PDFs into {output_path}")
        merger = PyPDF2.PdfMerger()
        
        # Add each PDF to the merger
        for pdf_path in pdf_paths:
            if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
                try:
                    merger.append(pdf_path)
                    logger.info(f"Added {pdf_path} to merged PDF")
                except Exception as e:
                    logger.warning(f"Could not add {pdf_path} to merged PDF: {e}")
        
        # Write the merged PDF to file
        merger.write(output_path)
        merger.close()
        
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            logger.info(f"Successfully created merged PDF at {output_path}")
            return True
        else:
            logger.error(f"Merged PDF was not created or is empty: {output_path}")
            return False
            
    except Exception as e:
        logger.error(f"Error merging PDFs: {e}", exc_info=True)
        return False

def process_url_worker():
    """Worker to process URLs from the queue."""
    while True:
        job_id, url, max_depth = url_queue.get()
        try:
            job_status[job_id]['status'] = 'processing'
            logger.info(f"Processing job {job_id} for URL: {url}")
            
            # Create a dedicated folder for this job
            job_folder = os.path.join(UPLOAD_FOLDER, job_id)
            os.makedirs(job_folder, exist_ok=True)
            
            # Create PDF for the main page
            main_filename = get_filename_from_url(url) + '.pdf'
            main_pdf_path = os.path.join(job_folder, main_filename)
            
            logger.info(f"Converting main URL to PDF: {url}")
            main_success = url_to_pdf(url, main_pdf_path)
            
            if main_success:
                logger.info(f"Successfully created PDF for main URL: {url}")
                job_status[job_id]['main_pdf'] = main_filename
            else:
                logger.error(f"Failed to create PDF for main URL: {url}")
                job_status[job_id]['main_pdf'] = None
                job_status[job_id]['main_error'] = f"Failed to convert {url} to PDF"
            
            # Track all successful PDFs for later merging
            successful_pdfs = []
            if main_success:
                successful_pdfs.append(main_pdf_path)
            
            # Extract and process URLs if requested
            if max_depth > 0:
                logger.info(f"Extracting links from URL: {url}")
                urls = extract_urls(url)
                logger.info(f"Found {len(urls)} links to process")
                
                job_status[job_id]['total_urls'] = len(urls)
                job_status[job_id]['processed_urls'] = 0
                job_status[job_id]['successful_urls'] = 0
                job_status[job_id]['failed_urls'] = 0
                job_status[job_id]['pdfs'] = []
                job_status[job_id]['failed_details'] = []
                
                # Process only first 50 URLs to avoid overload
                max_urls = min(len(urls), 50)
                if max_urls < len(urls):
                    logger.info(f"Limiting processing to first {max_urls} URLs")
                
                for i, sub_url in enumerate(urls[:max_urls]):
                    logger.info(f"Processing URL {i+1}/{max_urls}: {sub_url}")
                    
                    # Generate a unique filename for this URL
                    sub_filename = get_filename_from_url(sub_url) + '.pdf'
                    # Make sure filename is unique by adding a suffix if needed
                    counter = 1
                    base_name = os.path.splitext(sub_filename)[0]
                    while os.path.exists(os.path.join(job_folder, sub_filename)):
                        sub_filename = f"{base_name}_{counter}.pdf"
                        counter += 1
                        
                    sub_pdf_path = os.path.join(job_folder, sub_filename)
                    
                    try:
                        success = url_to_pdf(sub_url, sub_pdf_path)
                        job_status[job_id]['processed_urls'] += 1
                        
                        if success:
                            logger.info(f"Successfully created PDF for: {sub_url}")
                            job_status[job_id]['successful_urls'] += 1
                            job_status[job_id]['pdfs'].append({
                                'url': sub_url,
                                'filename': sub_filename
                            })
                            successful_pdfs.append(sub_pdf_path)
                        else:
                            logger.warning(f"Failed to create PDF for: {sub_url}")
                            job_status[job_id]['failed_urls'] += 1
                            job_status[job_id]['failed_details'].append({
                                'url': sub_url,
                                'error': 'Failed to convert to PDF'
                            })
                            
                        # Make sure to update the job status frequently
                        if i % 5 == 0:
                            job_status[job_id]['last_updated'] = time.time()
                            
                    except Exception as e:
                        logger.error(f"Error processing URL {sub_url}: {e}", exc_info=True)
                        job_status[job_id]['processed_urls'] += 1
                        job_status[job_id]['failed_urls'] += 1
                        job_status[job_id]['failed_details'].append({
                            'url': sub_url,
                            'error': str(e)
                        })
            
            # Create a merged PDF of all successful PDFs
            if successful_pdfs:
                merged_filename = f"combined_{job_id}.pdf"
                merged_pdf_path = os.path.join(job_folder, merged_filename)
                
                logger.info(f"Creating combined PDF from {len(successful_pdfs)} individual PDFs")
                if merge_pdfs(successful_pdfs, merged_pdf_path):
                    job_status[job_id]['merged_pdf'] = merged_filename
                    logger.info("Successfully created combined PDF")
                else:
                    job_status[job_id]['merged_pdf'] = None
                    logger.error("Failed to create combined PDF")
            else:
                logger.warning("No PDFs to merge, skipping combined PDF creation")
                job_status[job_id]['merged_pdf'] = None
            
            job_status[job_id]['status'] = 'completed'
            logger.info(f"Job {job_id} completed successfully")
            
        except Exception as e:
            logger.error(f"Job {job_id} failed with error: {e}", exc_info=True)
            job_status[job_id]['status'] = 'failed'
            job_status[job_id]['error'] = str(e)
        finally:
            url_queue.task_done()

# Start worker thread
worker_thread = threading.Thread(target=process_url_worker, daemon=True)
worker_thread.start()

@app.route('/')
def index():
    """Render the main page."""
    return render_template('index.html')

@app.route('/convert', methods=['POST'])
def convert():
    """Handle URL conversion request."""
    url = request.form.get('url')
    max_depth = int(request.form.get('max_depth', 1))
    
    if not url:
        flash('Please enter a URL', 'error')
        return redirect(url_for('index'))
    
    # Generate a unique job ID
    job_id = str(uuid.uuid4())
    
    # Initialize job status
    job_status[job_id] = {
        'status': 'queued',
        'url': url,
        'max_depth': max_depth,
        'created_at': time.time(),
        'merged_pdf': None  # Initialize merged PDF status
    }
    
    # Add job to queue
    url_queue.put((job_id, url, max_depth))
    
    return redirect(url_for('job_status_page', job_id=job_id))

@app.route('/status/<job_id>')
def job_status_page(job_id):
    """Render the job status page."""
    if job_id not in job_status:
        flash('Job not found', 'error')
        return redirect(url_for('index'))
    
    return render_template('status.html', job_id=job_id)

@app.route('/api/status/<job_id>')
def get_job_status(job_id):
    """Get the status of a job."""
    if job_id not in job_status:
        return jsonify({'error': 'Job not found'}), 404
    
    return jsonify(job_status[job_id])

@app.route('/download/<job_id>/<filename>')
def download_file(job_id, filename):
    """Download a generated PDF file."""
    return send_from_directory(os.path.join(UPLOAD_FOLDER, job_id), filename)

@app.route('/cleanup_old_jobs')
def cleanup_old_jobs():
    """Clean up old jobs (admin only)."""
    # In a real app, you'd add authentication here
    current_time = time.time()
    count = 0
    
    for job_id, status in list(job_status.items()):
        # Remove jobs older than 24 hours
        if current_time - status.get('created_at', 0) > 86400:
            job_folder = os.path.join(UPLOAD_FOLDER, job_id)
            if os.path.exists(job_folder):
                for file in os.listdir(job_folder):
                    os.remove(os.path.join(job_folder, file))
                os.rmdir(job_folder)
            del job_status[job_id]
            count += 1
    
    return jsonify({'message': f'Removed {count} old jobs'})

@app.route('/troubleshoot')
def troubleshoot():
    """Troubleshoot page to check system configuration."""
    results = {
        'system_info': {},
        'wkhtmltopdf_check': {},
        'dependencies': {}
    }
    
    # System info
    import platform
    results['system_info']['platform'] = platform.platform()
    results['system_info']['python_version'] = platform.python_version()
    
    # Check wkhtmltopdf
    import subprocess
    try:
        # Try common paths
        wkhtmltopdf_paths = [
            'wkhtmltopdf',  # If in PATH
            '/usr/bin/wkhtmltopdf',
            '/usr/local/bin/wkhtmltopdf',
            'C:\\Program Files\\wkhtmltopdf\\bin\\wkhtmltopdf.exe',
            '/opt/homebrew/bin/wkhtmltopdf'
        ]
        
        for path in wkhtmltopdf_paths:
            try:
                result = subprocess.run([path, '--version'], 
                                       stdout=subprocess.PIPE, 
                                       stderr=subprocess.PIPE,
                                       text=True,
                                       timeout=5)
                if result.returncode == 0:
                    results['wkhtmltopdf_check']['path'] = path
                    results['wkhtmltopdf_check']['version'] = result.stdout.strip()
                    results['wkhtmltopdf_check']['status'] = 'found'
                    break
            except (subprocess.SubprocessError, FileNotFoundError):
                continue
        else:
            results['wkhtmltopdf_check']['status'] = 'not_found'
            results['wkhtmltopdf_check']['error'] = 'wkhtmltopdf not found in common paths'
    except Exception as e:
        results['wkhtmltopdf_check']['status'] = 'error'
        results['wkhtmltopdf_check']['error'] = str(e)
    
    # Check dependencies
    try:
        import pkg_resources
        for pkg in ['flask', 'requests', 'beautifulsoup4', 'pdfkit']:
            try:
                version = pkg_resources.get_distribution(pkg).version
                results['dependencies'][pkg] = {'status': 'installed', 'version': version}
            except pkg_resources.DistributionNotFound:
                results['dependencies'][pkg] = {'status': 'not_installed'}
    except Exception as e:
        results['dependencies']['error'] = str(e)
    
    # Return HTML page with results
    return render_template('troubleshoot.html', results=results)
@app.route('/merge/<job_id>', methods=['POST'])
def merge_job_pdfs(job_id):
    """Manually trigger merging of all PDFs for a job."""
    if job_id not in job_status:
        flash('Job not found', 'error')
        return redirect(url_for('index'))
    
    job_folder = os.path.join(UPLOAD_FOLDER, job_id)
    
    # Collect all PDF paths
    pdf_paths = []
    
    # Add main PDF if it exists
    if job_status[job_id].get('main_pdf'):
        main_pdf_path = os.path.join(job_folder, job_status[job_id]['main_pdf'])
        if os.path.exists(main_pdf_path):
            pdf_paths.append(main_pdf_path)
    
    # Add all successful sub-PDFs
    if 'pdfs' in job_status[job_id]:
        for pdf_info in job_status[job_id]['pdfs']:
            pdf_path = os.path.join(job_folder, pdf_info['filename'])
            if os.path.exists(pdf_path):
                pdf_paths.append(pdf_path)
    
    if not pdf_paths:
        flash('No PDFs available to merge', 'warning')
        return redirect(url_for('job_status_page', job_id=job_id))
    
    # Create merged PDF
    merged_filename = f"combined_{job_id}.pdf"
    merged_pdf_path = os.path.join(job_folder, merged_filename)
    
    if merge_pdfs(pdf_paths, merged_pdf_path):
        job_status[job_id]['merged_pdf'] = merged_filename
        flash('PDFs successfully merged', 'success')
    else:
        flash('Failed to merge PDFs', 'error')
    
    return redirect(url_for('job_status_page', job_id=job_id))
    
if __name__ == '__main__':
    logger.info("Starting Web to PDF application")
    
    # Check if wkhtmltopdf is installed
    try:
        config = pdfkit.configuration()
        logger.info(f"Using wkhtmltopdf at: {config.wkhtmltopdf}")
    except Exception as e:
        logger.error(f"Error with wkhtmltopdf configuration: {e}")
        logger.error("Make sure wkhtmltopdf is installed and in your PATH")
    
    app.run(debug=True)
