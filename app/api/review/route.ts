import { NextResponse } from "next/server";

export async function POST(req: Request) {
  try {
    const { code, language } = await req.json();

    if (!process.env.GROQ_API_KEY) {
      return NextResponse.json({
        review: "❌ GROQ API key missing in .env.local",
      });
    }

    const response = await fetch(
      "https://api.groq.com/openai/v1/chat/completions",
      {
        method: "POST",

        headers: {
          Authorization: `Bearer ${process.env.GROQ_API_KEY}`,
          "Content-Type": "application/json",
        },

        body: JSON.stringify({
          model: "llama-3.1-8b-instant",

          messages: [
            {
              role: "system",
              content:
                "You are a senior developer. Review the code and explain errors clearly.",
            },

            {
              role: "user",
              content: `Review this ${language} code:\n\n${code}`,
            },
          ],

          temperature: 0.3,
        }),
      }
    );

    const data = await response.json();

    console.log("Groq Response:", data);

    if (!response.ok) {
      return NextResponse.json({
        review:
          "❌ " +
          (data?.error?.message ||
            "API request failed"),
      });
    }

    return NextResponse.json({
      review:
        data.choices[0].message.content,
    });

  } catch (error) {
    console.error("Server Error:", error);

    return NextResponse.json({
      review:
        "❌ Server error occurred.",
    });
  }
}