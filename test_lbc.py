"""
Test the Leboncoin bookmarklet extraction logic locally.
Simulates what the bookmarklet would extract and send.

Run: python test_lbc.py
"""
import json

# Simulated data as the bookmarklet would extract from the LBC table
sample_listings = [
    {"title": "4x Pneus été 205/55 R17 95V Michelin + Montage", "sku": "1804", "price": 240},
    {"title": "4x Pneus été 235/55 R19 105V Continental + Montage", "sku": "1784", "price": 260},
    {"title": "4x Pneus été 215/55 R18 99V Michelin + Montage", "sku": "1798", "price": 250},
]

payload = {"listings": sample_listings}

print("=== Bookmarklet Payload Test ===")
print(f"Would send {len(sample_listings)} listings to POST /api/listings/import-leboncoin")
print(f"\nPayload:\n{json.dumps(payload, indent=2)}")

# Test the extraction JS logic (same as bookmarklet)
print("\n=== Bookmarklet JS (for Chrome console on LBC) ===")
print("""
Paste this in Chrome console on https://www.leboncoin.fr/compte/pro/mon-activite :

var d=document.querySelectorAll('tbody tr'),r=[];
d.forEach(function(row){
  var c=row.querySelectorAll('td');
  if(c.length<4) return;
  var h=c[2].querySelector('h3');
  var p=c[2].querySelector('p.truncate');
  var pr=c[3].textContent.trim().replace(/[^0-9]/g,'');
  if(h) r.push({
    title: h.textContent.trim(),
    sku: p ? p.textContent.trim() : '',
    price: pr ? parseInt(pr) : null
  });
});
console.log('Found', r.length, 'listings');
console.log(JSON.stringify(r, null, 2));
""")

print("\n=== Test complete ===")
print("Next: paste the extraction JS in Chrome console on your LBC page")
print("and verify the output matches your listings.")
