# Masai Downloader — Fix v1.7 (sirf app.py badla hai)

## Pehle, pichli jaanch ka pakka natija

Token service ke Logs ne saaf dikha diya (9 Nov, 09:19 AM):
**app token asal me le rahi hai.** POT service ne app ki maang par
us video (LGe0xwz5cv4) ke liye token banakar diye. Matlab:

- POT_BASE_URL sahi laga hai ✅
- Render par plugin chal raha hai ✅
- Token service sahi jawab de rahi hai ✅

Phir bhi YouTube formats nahi de raha. Bachi hui sabse majboot wajah:
**YouTube, Render ke Singapore wale server-pate (IP) aur is login ki
jodi ko sabse sakht wali rok laga raha hai** — bina login ke token ke
saath bhi bot deewaar, login ke saath deewaar paar lekin formats band.
(Wahi token-jod kisi aam pate se bilkul chalti hai — woh pariksha ho
chuki hai.)

## v1.7 me kya badla — aakhri teen login-wale roop

v1.6 ki koshishon ke baad teen aise client roop bache the jo **login ke
saath kabhi try hi nahi hue**, aur ye teeno formats aksar dusre raste
se dete hain:

1. **ios + login** — iPhone wala client; formats playlist (HLS) ke
   raste deta hai, jahan SABR wali rok aksar nahi lagti
2. **creator + login** — YouTube Studio ka apna client (web_creator);
   tumhara account Studio khol sakta hai, isliye ye rasta khula hai
3. **android + login** — Android app wala client login ke saath

Poora kram ab: web+login → web+token → **ios+login → creator+login →
android+login** → tv → tv+login → visionos → visionos+login →
embedded+login → embedded (kul 11 koshishein).

## Saath me ek zaroori sudhaar — ab koi saboot chhupega nahi

v1.6 ke test me error aadha kat gaya tha, isliye aadhi koshishon ka
natija dikha hi nahi. v1.7 me **har koshish ki galti task ke saath alag
se save hoti hai** — /task-status ke jawab me `attempts` naam se poori
suchi milegi. Is baar jo hoga, poora dikhega.

/health me ab `build: v1.7` dikhega.

## Tumhe kya karna hai

1. ZIP se **sirf `app.py`** overwrite karo
2. `git push`
3. "Push ho gaya" bolo — test mai khud karunga (is baar test thoda
   lamba chal sakta hai, 11 koshishon ki wajah se ~2-4 minute)

## Imandaar baat

Ye code ki taraf se **aakhri bacha hua daav** hai. Agar ye teenon naye
roop bhi formats na dein, to baat pakki ho jayegi ki rok code se paar
nahi hogi — phir do hi raste bachenge, jinme se ek tumhe chunna hoga:
(1) chhota sa paid **residential proxy** (server ka pata aam ghar-wale
internet jaisa dikhega), ya (2) YouTube tab ko server se hata kar
sirf Masai/Direct tab rakhna. Us faisle ke liye mai dono ke daam aur
tafseel taiyaar karke dunga — pehle v1.7 ka natija dekh lete hain.
