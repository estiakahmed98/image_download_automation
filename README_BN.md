# Deshi Plus — Product Image Downloader (Official-first)

`Products Name.txt` থেকে প্রতি লাইনে একটি পণ্যের নাম পড়ে brand-এর official product page থেকে আসল resolution-এর ছবি সংগ্রহ করার চেষ্টা করে। Official image না পেলে exact-model image search থেকে fallback নেয়। ছবি **PNG এবং lossless WebP** দুই ফরম্যাটেই রাখে। কোনো ছবি upscale/crop করে না।

## Windows: প্রথমবার চালানো

1. Python **3.10 বা নতুন** install করুন (`Add python.exe to PATH` সিলেক্ট করুন)।
2. ZIP extract করে **`setup_windows.bat`** চালান।
3. `Products Name.txt`-এ নিজের product name লিখুন: **প্রতি লাইনে একটি নাম, UTF-8 encoding**। Sample ৩০টি নাম রাখা হয়েছে, চাইলে replace করুন।
4. প্রথমে **`run_10.bat`** চালিয়ে ১০টি product test করুন।
5. ঠিক থাকলে **`run_all.bat`** চালান। বন্ধ/ক্র্যাশ হলে একই command আবার চালান—completed products skip করবে।

কমান্ড দিয়ে চালাতে চাইলে PowerShell-এ:

```powershell
cd "D:\YourFolder\Product_Image_Downloader"
.venv\Scripts\python.exe main.py --category "Accessories/Keyboard" --limit 10 --max-images 5
.venv\Scripts\python.exe main.py --category "Accessories/Keyboard" --max-images 5
```

`--max-images 5` মানে **মোট পাঁচটি** ভিন্ন ছবি: `Product Name.png`, `Product Name 1.png` ... `Product Name 4.png` (একই naming-এর `.webp`)। প্রথম ফাইলসহ **ছয়টি** চাইলে `--max-images 6` লিখুন। সব product-এর পাঁচটি ছবি পাওয়া নিশ্চিত নয়।

## ফোল্ডার

```text
Product_Image_Downloader/
  Products Name.txt
  brands.json
  main.py
  setup_windows.bat
  run_10.bat
  run_all.bat
  output/
    Accessories/
      Keyboard/
        1STPLAYER/
          1STPLAYER KM2 Keyboard Mouse Combo.png
          1STPLAYER KM2 Keyboard Mouse Combo.webp
          1STPLAYER KM2 Keyboard Mouse Combo 1.png
          1STPLAYER KM2 Keyboard Mouse Combo 1.webp
          .1STPLAYER KM2 Keyboard Mouse Combo.manifest.json
          1STPLAYER NEO87 Keyboard.png
          ...
        8BitDo/
          8BitDo Retro 108 Tri-Mode Wireless Mechanical Keyboard.png
          ...
  reports/
    products_status.csv
    image_sources.csv
```

- প্রতিটি brand-এর জন্য একটি folder হয়; একই brand-এর সব পণ্যের image সেই folder-এর সরাসরি ভেতরে থাকে।
- আলাদা hidden manifest file প্রতিটি product-এর resume তথ্য রাখে; এতে একই brand-এর অন্য image মুছে যায় না।
- `products_status.csv`: প্রতিটি product-এর **COMPLETE**, **PARTIAL**, **FAILED**, **REVIEW_RIGHTS** স্ট্যাটাস।
- `image_sources.csv`: কোন ছবি কোন URL, কোন page, কোন resolution থেকে এসেছে।
- `manifest.json`: item-specific resume তথ্য।
- `REVIEW_RIGHTS`: third-party উৎস থেকে আসা ছবি। **Publish করার আগে model/variant এবং reuse permission অবশ্যই যাচাই করুন।**

## গুরুত্বপূর্ণ commands

```powershell
# শুধু manufacturer site থেকে সংগ্রহ
.venv\Scripts\python.exe main.py --official-only --max-images 5

# ২০টি, নতুন category-তে
.venv\Scripts\python.exe main.py --category "Component/SSD" --limit 20

# আগের PARTIAL product-গুলোর বাকি ছবি খোঁজা; পূর্বের ছবি না মুছে
.venv\Scripts\python.exe main.py --retry-incomplete

# আগের output নতুন করে সংগ্রহ (সেই product-এর পুরনো image বাদ দেবে)
.venv\Scripts\python.exe main.py --force

# Site-এ JavaScript-only product gallery থাকলে ঐচ্ছিক browser render
.venv\Scripts\python.exe -m pip install playwright
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe main.py --browser --limit 10

# Resolution threshold একটু কমানো (কম ছবি পাওয়া গেলে)
.venv\Scripts\python.exe main.py --min-side 250 --retry-incomplete
```

## Brand configuration

`brands.json`-এ নাম আর approved **official domain** দিন। যেমন:

```json
{
  "1STPLAYER": ["1stplayer.com"],
  "8BitDo": ["8bitdo.com"],
  "A4TECH": ["a4tech.com", "bloody.com"]
}
```

নতুন brand না থাকলে brands.json-এ add করুন। Brand name প্রোডাক্ট নামের শুরুতে থাকা উচিত। Model token অটোমেটিক চিনতে না পারলে system ভুল মডেলের ছবি নেওয়ার বদলে FAILED/Review করে। কিছু নামের মধ্যে model নেই বা model inference কঠিন—সেগুলোর জন্য ম্যানুয়াল সমাধান দরকার হবে।

## বাস্তব সীমাবদ্ধতা

- এটি **প্রথম সংস্করণ**, live multi-site success guarantee নয়। প্রতিটি brand-এর HTML আলাদা। Javascript, anti-bot, CAPTCHAs, search rate limits, robots.txt, hotlink protection থাকলে skip করবে।
- Search matching একটি **text heuristic**; ১০০% model/variant verified নয়। Publish করার আগে ছবি দেখে confirm করুন; especially variant/color/switch।
- কোনো CAPTCHA bypass, access restriction bypass, rate-limit circumvention করা হয় না। Website-এর terms এবং image usage permission মানতে হবে। Manufacturer-এর ছবি হলেও commercial reuse automatic অনুমোদিত নয়।
- TXT reading খুব দ্রুত; আসল সময় যায় web searching এবং downloading-এ। অনেক হাজার পণ্যের জন্য API/licensed distributor feed সবচেয়ে reliable/scalable।
- Progress ও report file নিয়মিত লেখা হয়। Python 3.10+ এবং Internet প্রয়োজন।

Docs: https://github.com/deedy5/ddgs (search library); https://pillow.readthedocs.io/ (image processing).
