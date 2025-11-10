import express from "express";
import bodyParser from "body-parser";
import fetch from "node-fetch";

const app = express();
app.use(bodyParser.json());

const PAGE_ACCESS_TOKEN = "EAAT94bylvZAkBPZB2qPFn8LDrcZBP3IXJZA2JZB4Dv0kr8g0fZBHLpEtX6j0YgecyUpewlf6064LdMOwZC6xWfkknsoZBZBa1p9A66lmbwpraBzbuMcon1BHom1bX9wx7ZBdv7lVdhIcrmiJiYln86rImZCZAzni0Mk0qtvhfbh8nfWrStzmFXVb1fzsxcYZCUpvV1hilctioxFZBFWJfaM5LeoNe7SzUmXwZDZD"; // ჩასვი შენი ტოკენი აქ

// ვებჰუქის შემოსული შეტყობინება
app.post("/webhook", async (req, res) => {
  const body = req.body;

  if (body.object === "page") {
    for (const entry of body.entry) {
      const event = entry.messaging[0];
      const senderId = event.sender.id;

      if (event.message && event.message.text) {
        const userMessage = event.message.text.toLowerCase();

        // აქ შეგიძლია raiders.ge-ზე პროდუქტის ძიება
        // მარტივად ვაჩვენებ დემო ვარიანტს:
        if (userMessage.includes("nike")) {
          await sendMessage(senderId, "👉 აი Nike პროდუქცია: https://raiders.ge/search?q=nike");
        } else {
          await sendMessage(senderId, `ვერ ვიპოვე "${userMessage}". სცადე სხვა სიტყვა 🛍`);
        }
      }
    }

    res.status(200).send("EVENT_RECEIVED");
  } else {
    res.sendStatus(404);
  }
});

// შეტყობინების გაგზავნა
async function sendMessage(senderId, text) {
  await fetch(`https://graph.facebook.com/v19.0/me/messages?access_token=${PAGE_ACCESS_TOKEN}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      recipient: { id: senderId },
      message: { text },
    }),
  });
}

// Verification endpoint
app.get("/webhook", (req, res) => {
  const VERIFY_TOKEN = "raiders_verify";
  const mode = req.query["hub.mode"];
  const token = req.query["hub.verify_token"];
  const challenge = req.query["hub.challenge"];

  if (mode && token === VERIFY_TOKEN) {
    res.status(200).send(challenge);
  } else {
    res.sendStatus(403);
  }
});

app.listen(3000, () => console.log("✅ Raiders GE Messenger bot is running"));

