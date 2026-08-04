# BIST Scanner Pro — GitHub Pages Cloud Beta

Telefon ve bilgisayardan aynı internet bağlantısıyla açılan statik/PWA sürümüdür.

## Kurulum
1. GitHub'da yeni, herkese açık bir depo oluşturun (ör. `bist-scanner-pro`).
2. Bu ZIP'in **içindeki dosyaları** deponun köküne yükleyin.
3. Depoda **Settings → Pages → Source** bölümünde `GitHub Actions` seçin.
4. **Actions** sekmesine girip `BIST Cloud Snapshot ve GitHub Pages` iş akışını `Run workflow` ile bir kez çalıştırın.
5. İşlem tamamlanınca adresiniz `https://KULLANICI_ADI.github.io/DEPO_ADI/` biçiminde açılır.

## Çalışma şekli
- GitHub Actions hafta içi Türkiye saatiyle yaklaşık 19:35'te BIST Tüm evrenini 4 parçaya bölerek tarar ve tek sonuçta birleştirir.
- Piyasa kartları ve KAP bildirimleri snapshot olarak yenilenir.
- Screener AI ve Decision Center, indirilen günlük tarama verisini telefonda/tarayıcıda filtreler.
- GitHub Pages Python çalıştırmadığı için anlık tarama ve yeni backtest yerine son snapshot kullanılır.

## Manuel güncelleme
Actions sekmesinden iş akışını istediğiniz zaman elle çalıştırabilirsiniz.


## BIST Tüm parçalı tarama

GitHub Actions, KAP kaynaklı güncel pay kodlarını alır; evreni 4 dengeli parçaya böler. Parçalar en fazla ikişer adet paralel taranır ve sonuçlar `data/last_scan.json` içinde tek veri kümesine birleştirilir. `data/build_info.json` dosyası taranan, tamamlanan ve başarısız sembol sayılarını içerir.
