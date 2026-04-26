import json
d = json.load(open('benchmarks/ground_truth.json'))
apps = {b['app'] for b in d['bugs']}
print(f"Total bugs: {len(d['bugs'])}")
for a in sorted(apps):
    count = sum(1 for b in d['bugs'] if b['app'] == a)
    print(f"  {a}: {count}")
print(f"Apps defined: {list(d['apps'].keys())}")
