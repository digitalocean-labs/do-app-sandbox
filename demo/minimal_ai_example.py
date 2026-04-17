"""
Minimal Example: LLM Code Generation + Sandbox Execution

A simple 30-line example showing the basic workflow:
1. Generate code with Groq (free tier)
2. Execute it in a sandbox
3. Get the output

Get a free API key at: https://console.groq.com
Run with: GROQ_API_KEY="gsk_..." python minimal_ai_example.py
"""

import os
from openai import OpenAI
from do_app_sandbox import Sandbox

# Initialize Groq client (free tier, OpenAI-compatible SDK)
# Get your free API key at: https://console.groq.com
client = OpenAI(
    api_key=os.environ["GROQ_API_KEY"],
    base_url="https://api.groq.com/openai/v1",
)

# Step 1: Generate Python code using Groq (llama-3.3-70b)
print("🤖 Generating code with Groq (llama-3.3-70b-versatile)...\n")

prompt = """Generate a Python script that:
1. Creates a list of numbers 1-10
2. Calculates the sum, average, and product
3. Prints formatted results

Output ONLY the code, wrapped in ```python ... ```"""

response = client.chat.completions.create(
    model="llama-3.3-70b-versatile",
    messages=[{"role": "user", "content": prompt}],
    temperature=0.7,
    max_tokens=500,
)

# Extract code from response
generated_code = response.choices[0].message.content
# Remove markdown code block if present
generated_code = generated_code.replace("```python\n", "").replace("```", "").strip()

print("📝 Generated code:")
print("-" * 50)
print(generated_code)
print("-" * 50 + "\n")

# Step 2: Create sandbox and execute the code
print("🏗️  Creating sandbox...\n")
sandbox = Sandbox.create(image="python", name=f"ai-agent-{int(os.environ.get('RANDOM', 123))}")

try:
    # Note: The Python image uses uv as package manager
    # To install packages:
    #   - Use: sandbox.exec("uv pip install package_name")
    #   - Or:  sandbox.exec("source /app/.venv/bin/activate && pip install package_name")
    # 
    # Direct "pip install" fails due to PEP 668 (externally-managed-environment)

    # Write code to file
    sandbox.filesystem.write_file("/app/generated_code.py", generated_code)

    # Execute
    print("⚙️  Executing code...\n")
    result = sandbox.exec("python3 /app/generated_code.py")

    # Step 3: Show results
    print("📤 Output:")
    print("=" * 50)
    print(result.stdout)
    print("=" * 50)

    if result.exit_code == 0:
        print("\n✓ SUCCESS!")
    else:
        print(f"\n✗ FAILED (exit code {result.exit_code})")
        if result.stderr:
            print(f"Error:\n{result.stderr}")

finally:
    # Cleanup
    print("\n🧹 Cleaning up...")
    sandbox.delete()
