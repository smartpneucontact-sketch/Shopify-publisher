// === PASTE THIS IN CHROME CONSOLE ON https://www.leboncoin.fr/mes-annonces ===
// It will extract listing data and show what we can grab.

(function() {
  console.log("=== SmartPneu LBC Extractor ===");

  // Method 1: Try __NEXT_DATA__
  const nextDataEl = document.getElementById('__NEXT_DATA__');
  if (nextDataEl) {
    try {
      const data = JSON.parse(nextDataEl.textContent);
      const props = data.props?.pageProps || {};
      console.log("__NEXT_DATA__ keys:", Object.keys(props));

      // Look for ads in various locations
      const possibleAds = props.ads || props.searchData?.ads || props.listings || props.myAds || props.results || [];
      if (possibleAds.length) {
        console.log(`Found ${possibleAds.length} ads in __NEXT_DATA__`);
        possibleAds.slice(0, 3).forEach((ad, i) => {
          console.log(`Ad ${i+1}:`, {
            title: ad.subject || ad.title,
            price: ad.price,
            list_id: ad.list_id || ad.id,
            url: ad.url,
            attributes: ad.attributes?.map(a => `${a.key}=${a.value}`).join(', ')
          });
        });
      } else {
        console.log("No ads array found in __NEXT_DATA__. Full pageProps:", JSON.stringify(props).slice(0, 2000));
      }
    } catch(e) {
      console.log("Error parsing __NEXT_DATA__:", e);
    }
  } else {
    console.log("No __NEXT_DATA__ found on this page");
  }

  // Method 2: Scrape the DOM for listing cards
  console.log("\n--- DOM Scraping ---");

  // Try common selectors for listing cards
  const selectors = [
    '[data-test-id="ad"]',
    '[data-qa-id="aditem_container"]',
    'a[href*="/ad/"]',
    '.styles_adCard',
    '[class*="adCard"]',
    '[class*="AdCard"]',
    'article',
    '[data-testid]',
  ];

  for (const sel of selectors) {
    const els = document.querySelectorAll(sel);
    if (els.length > 0) {
      console.log(`Selector "${sel}": ${els.length} elements`);
      // Show first element's HTML structure
      if (els[0]) {
        console.log("  First element tag:", els[0].tagName);
        console.log("  First element classes:", els[0].className?.toString().slice(0, 100));
        console.log("  First element innerHTML preview:", els[0].innerHTML?.slice(0, 300));
      }
    }
  }

  // Method 3: Find all links to ads
  const adLinks = document.querySelectorAll('a[href*="/ad/"]');
  console.log(`\nFound ${adLinks.length} ad links on page`);
  const uniqueUrls = [...new Set([...adLinks].map(a => a.href))];
  console.log(`Unique ad URLs: ${uniqueUrls.length}`);
  uniqueUrls.slice(0, 5).forEach(u => console.log("  ", u));

  // Method 4: Check for any JSON data in script tags
  console.log("\n--- Script tags with JSON ---");
  document.querySelectorAll('script[type="application/json"]').forEach((el, i) => {
    const preview = el.textContent.slice(0, 200);
    console.log(`Script ${i} (id=${el.id}):`, preview);
  });

  console.log("\n=== Done! Copy-paste this output back to Claude ===");
})();
