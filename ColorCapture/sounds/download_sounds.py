import urllib.request
import urllib.parse
import json
import os
import sys

def download_wikimedia_audio(query, filename):
    print(f"Searching for '{query}'...")
    search_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(query + ' filetype:audio')}&utf8=&format=json"
    
    try:
        req = urllib.request.Request(search_url, headers={'User-Agent': 'ColorCaptureAudioFetcher/1.0'})
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read())
            
            results = data.get('query', {}).get('search', [])
            if not results:
                print(f"No audio found for '{query}'.")
                return False
                
            title = results[0]['title']
            title = title.replace(' ', '_')
            
            # Get the actual file URL
            file_url_query = f"https://en.wikipedia.org/w/api.php?action=query&titles={urllib.parse.quote(title)}&prop=imageinfo&iiprop=url&format=json"
            req2 = urllib.request.Request(file_url_query, headers={'User-Agent': 'ColorCaptureAudioFetcher/1.0'})
            
            with urllib.request.urlopen(req2) as resp2:
                data2 = json.loads(resp2.read())
                pages = data2.get('query', {}).get('pages', {})
                for page_id, page_info in pages.items():
                    imageinfo = page_info.get('imageinfo', [])
                    if imageinfo:
                        url = imageinfo[0].get('url')
                        print(f"Downloading {url} to {filename}...")
                        
                        req3 = urllib.request.Request(url, headers={'User-Agent': 'ColorCaptureAudioFetcher/1.0'})
                        with urllib.request.urlopen(req3) as resp3:
                            with open(filename, 'wb') as f:
                                f.write(resp3.read())
                        print("Done.")
                        return True
                        
    except Exception as e:
        print(f"Error fetching {query}: {e}")
        return False

def main():
    dest_dir = "/home/catalina-antemir/FACULTATE/PERSONAL/LEDHACK/Bon-Bon/ColorCapture/sounds"
    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir)
    os.chdir(dest_dir)
    
    # We will use .ogg as Pygame handles it perfectly
    tasks = [
        ("gong", "gong.ogg"),
        ("applause crowd", "applause.ogg"),
        ("trumpet fanfare", "trumpet.ogg"),
        ("keygen music loop", "bgm.ogg")  # Some electronic/arcade music
    ]
    
    for query, fname in tasks:
        # Avoid downloading if already downloaded
        success = download_wikimedia_audio(query, fname)
        if not success:
            # fallback terms
            download_wikimedia_audio(query.split()[0], fname)

if __name__ == "__main__":
    main()
