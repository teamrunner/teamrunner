# Your first real debate

This example uses `teamrunner/demoLLM`, a free, limited service sponsored by
TeamRunner. No account or API key is needed. Prompts and earlier speeches are
sent to TeamRunner's server and its model provider (currently OpenRouter and
Alibaba). Do not include private information. The model may change; each turn
records the model that answered. This service is for trying small examples.

```sh
teamrunner new my_debate --from demo
teamrunner run my_debate --message "A four-day workweek improves productivity"
```

If the free allowance is reached, try later or change `default_model` in
`team.yaml` to your own provider/model and add that provider's API key.
For larger studies or toolkeepers, use your own model.
