# Instagram automatizacija -- uputstvo za postavku

Ova skripta automatski objavljuje reels-ove sa tvog Google Drive foldera na
Instagram, u krug, 5x dnevno, potpuno bez tvog ucesca nakon postavke.

## Sta vec imas spremno

- Access Token (Instagram)
- Instagram Business Account ID: 27608249732169712
- App Secret
- Google Drive folder ID: 1W9OBtKGDOPwp23k3J8rCY8S_kewflZEQ
- JSON fajl servisnog naloga (preuzet sa Google Cloud)

## Korak 1 -- Napravi GitHub nalog (ako ga nemas)

Idi na github.com i registruj se (besplatno).

## Korak 2 -- Napravi nov repozitorijum

1. Klikni "+" gore desno -> "New repository"
2. Nazovi ga npr. "instagram-bot"
3. Postavi ga na **Private** (bitno -- da niko drugi ne vidi tvoje podatke)
4. Klikni "Create repository"

## Korak 3 -- Otpremi ove fajlove

1. Na stranici tvog novog repozitorijuma, klikni "uploading an existing file"
   (ili "Add file" -> "Upload files")
2. Prevuci SVE fajlove i foldere iz ovog paketa (instagram_post.py,
   requirements.txt, i ceo .github folder sa workflow fajlom unutra)
3. Klikni "Commit changes"

Vazno: folder ".github/workflows/" mora ostati tacno tako imenovan i u toj
putanji da bi GitHub prepoznao raspored.

## Korak 4 -- Dodaj tajne podatke (Secrets)

Ovo je mesto gde bezbedno cuvas svoj token, ne u samom kodu.

1. Na repozitorijumu, idi na "Settings" (tab na vrhu)
2. Levi meni: "Secrets and variables" -> "Actions"
3. Klikni "New repository secret" i dodaj redom sledeca 4 secreta:

   - Ime: `IG_ACCESS_TOKEN`      Vrednost: tvoj Access Token
   - Ime: `IG_ACCOUNT_ID`        Vrednost: 27608249732169712
   - Ime: `GDRIVE_FOLDER_ID`     Vrednost: 1W9OBtKGDOPwp23k3J8rCY8S_kewflZEQ
   - Ime: `GDRIVE_SERVICE_ACCOUNT_JSON`  Vrednost: otvori preuzeti JSON fajl
     Notepad-om, selektuj SAV tekst (Ctrl+A), kopiraj (Ctrl+C) i nalepi ovde
     kao vrednost

Za svaki, klikni "Add secret" da sacuvas.

## Korak 5 -- Testiraj

1. Idi na tab "Actions" na repozitorijumu
2. Levo klikni na "Objavi Instagram Reel"
3. Desno klikni "Run workflow" -> "Run workflow" (zeleno dugme)
4. Sacekaj minut-dva, osvezi stranicu, klikni na pokrenuti "run" da vidis
   da li je uspeo (zelena kvacica) ili nije (crveni X, klikni da vidis
   gresku)

Ako je zelena kvacica -- cestitam, prvi video je objavljen na Instagram!
Od sada, ovo se desava samo, 5x dnevno, bez tebe.

## Kako dodajem nove videe kasnije?

Samo ih prevuces (upload) u isti Google Drive folder. Skripta ce ih sama
prepoznati sledeci put kad se pokrene i ukljuciti u rotaciju.

## Sta ako nesto zakaze?

Idi na tab "Actions" na GitHubu -- tu ces videti listu svih pokusaja
objavljivanja (uspesnih i neuspesnih) i mozes da klikneš na bilo koji da
vidis detaljnu poruku o gresci.

Najcesci uzrok gresaka posle nekog vremena: Access Token istekne posle
~60 dana i treba ga rucno obnoviti i zameniti u Secrets (IG_ACCESS_TOKEN).
