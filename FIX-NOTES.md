# Masai Downloader — Fix v1.6 (sirf app.py badla hai)

## Is baar kya hua (saboot ke saath)

v1.5 + POT + cookies ke baad YouTube ki sabse badi deewaar **gir gayi**:
ab "Sign in to confirm you're not a bot" nahi aata. Uske badle naya error
aaya — **"Requested format is not available"**.

Iska matlab: YouTube ne app ko video tak pahunchne to diya (token aur
login dono kubool hue), lekin **download wale formats rok diye**. Ye
YouTube ke naye **SABR** system ki wajah se hota hai — kuch login
sessions ke saath woh har client (web/TV/Android) par formats bina asli
URL ke deta hai, isliye koi format chuna hi nahi ja pata.

Local pariksha (usi video LGe0xwz5cv4 par) me pakka hua:
- **Bina login ke, sirf POT token ke saath** — wahi video saaf milta hai:
  47 formats, 1080p tak.
- Login wale session ke saath hi formats rukte hain.

## v1.6 me kya badla

App ab har kaam ke liye **(client set × login hai ya nahi) ki jodiyan**
ek ke baad ek try karti hai:

1. web + login (cookies + token) — pehle wali koshish
2. **web + sirf token (bina cookies)** — nayi, sabse zyada ummeed wali
3. TV set bina login ke
4. TV set login ke saath
5. visionos/Android bina login ke
6. visionos/Android login ke saath
7. embedded (login ke saath, phir bina login ke)

- Koi ek jodi chal jaye, kaam ho gaya. Sab fail ho jayein to error me
  **har koshish ki galti alag-alag** dikhegi (kaunsi jodi kahan atki),
  taaki agli baar andaza na lagana pade.
- "Formats roke gaye" wale error ke liye UI me ab alag, saaf sandesh
  aayega (bot wale sandesh se alag).
- Playlist download wala rasta bhi yahi nayi jodiyan istemal karta hai.
- /health me ab `build: v1.6` dikhega.

## Tumhe kya karna hai

1. Is ZIP se **sirf `app.py`** apne project me overwrite karo.
   (requirements.txt / render-build.sh v1.5 wale hi rahenge — is baar
   unme koi badlaav nahi.)
2. `git push` — teeno services par auto-deploy hoga, lekin test sirf
   **Singapore wali Videos-downloader** par hoga (usi par POT laga hai).
3. Bas "push ho gaya" bol do — /health aur video test **mai khud
   karunga** (usi Matrices wale video par pehle, phir ek gaane par).

## Ek imandaar baat

Ye fix local pariksha me bilkul chala hai (web+token jodi ne pehli hi
koshish me 47 formats diye). Lekin Render ke mahaul me asli saboot
push ke baad ke live test se hi milega — isiliye "fix ho gaya" mai
tabhi kahunga jab Singapore wali site se video asal me nikal aaye.
