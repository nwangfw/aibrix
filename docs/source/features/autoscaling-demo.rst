.. _Autoscaling Demo:

====================
Autoscaling Demo
====================



Dataset Downloading
---------------------


We first need to download the public ShareGPT dataset, which will be later used to generate our experiment workloads. 

.. code-block:: shell

    wget https://huggingface.co/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered/resolve/main/ShareGPT_V3_unfiltered_cleaned_split.json -O /tmp/ShareGPT_V3_unfiltered_cleaned_split.json


Then, we can generate a synthetic workload using the following command:

.. code-block:: shell

    cd benchmarks/generator
    export SHAREGPT_FILE_PATH=/tmp/ShareGPT_V3_unfiltered_cleaned_split.json
    python workload_generator.py --prompt-file $SHAREGPT_FILE_PATH --interval-ms 1000 --duration-ms 300000 --trace-type synthetic --traffic-pattern "quick_rising" --prompt-len-pattern "slight_fluctuation" --completion-len-pattern "slight_fluctuation" --output-dir "./output" --output-format jsonl 

After running the above command, you will find the generated workload called "synthetic_manual_config.jsonl" in the ``output`` directory. The following are some samples from the dataset:

.. code-block:: shell

    {"timestamp": 536, "requests": [{"prompt": "It seems that you are running into an error because the option name \"maxSize\" is not a valid format. According to the Discord.js documentation, option names must be 1-32 characters long and can only contain lowercase letters, numbers, and symbols - and \\_.\n\nIn order to add a maxSize option to your /save command, you will need to change the name of the option to something that meets these constraints and then update the code in the execute function to check the maxSize and compare it with attachment size before saving the image.\n\nHere is an example of how you can update your code to include a maxSize option:\n```\nconst { SlashCommandBuilder } = require(\"discord.js\");\nconst fs = require(\"fs\");\nconst path = require(\"path\");\nconst https = require(\"https\");\nconst http = require(\"http\");\n\nmodule.exports = {\n  data: new SlashCommandBuilder()\n    .setName(\"save\")\n    .setDescription(\"Save the last X images in the channel.\")\n    .addIntegerOption((option) =>\n      option.setName(\"amount\").setDescription(\"Number of images to save\")\n    )\n    .addIntegerOption((option) =>\n      option\n        .setName(\"max_size\")\n        .setDescription(\"Maximum size of attachments in KB\")\n    ),\n\n  async execute(interaction) {\n    const amount = interaction.options.getInteger(\"amount\");\n    const maxSize = interaction.options.getInteger(\"max_size\") * 1000;\n\n    if (amount < 1 || amount > 99) {\n      return interaction.reply({\n        content: \"You need to input a number between 1 and 99.\",\n        ephemeral: true,\n      });\n    }\n\n    // Retrieve the last X images in the channel\n    const messages = await interaction.channel.messages.fetch({\n      limit: amount,\n    });\n    console.log(\"Retrieved messages:\", messages.size);\n    const images = messages.filter((message) => message.attachments.size > 0);\n    console.log(\"Retrieved images:\", images.size);\n\n    // Save the images to the local disk\n    const channelName = interaction.channel.name;\n    const dir = `./images/${\n      channelName ? channelName.replace(/[\\\\\\/:*?\"<>|]/g, \"\") : \"default\"\n    }`;\n    console.log(\"path: \" + dir);\n    if (!fs.existsSync(dir)) {\n      fs.mkdirSync(dir);\n      console.log(\"folder created: \" + dir);\n    }\n\n    let savedImagesCount = 0;\n    const allowedExtensions = [\".jpg\", \".jpeg\", \".png\", \".gif\"];\n\n    for (const [i, message] of images.entries()) {\n      for (const [j, attachment] of message.attachments.entries()) {\n        const fileName = attachment.name;\n        const extension = path.extname(fileName).toLowerCase();\n        if (!allowedExtensions.includes(extension)) {\n          console.log(\n            `Skipping file ${fileName} because it has an invalid extension.`\n          );\n          continue;\n        }\n        if (attachment.size > maxSize) {\n          console.log(\n            `Skipping file ${fileName\n```", "model": "Qwen/Qwen2.5-Coder-7B-Instruct", "prompt_length": 629, "output_length": 794}]}
    {"timestamp": 666, "requests": [{"prompt": "Can you draft an SOW based on the following partial information? If there are sections of a typical SOW that this information does not address, note them and leave them blank.\n\nThis executive sprint course is a combination of executive-level educational experiences tailored to your specific focus and needs. \n\nOur approach blends traditional workshopping, with curated education modules comprising of brief videos, reading content and exercises. All of this is combined to create milestone driven progress among participants ensuring your investment of time and energy sticks and lasts.\n\nWhat you\u2019ll get:\nThis 6-week dynamic learning experience is tailored to help you thrive. You will learn strategies and tools to leverage your firm\u2019s resources and clients so that new value creation opportunities can be discovered. You will learn and apply new design techniques and methods to find, frame, and communicate client value opportunities in contemporary and compelling ways. \n\nLearning Delivery\n1. Pre-program design session with program stakeholders\n2. Participant onboarding design, consisting of\na. Email invitations with program description, learning outcomes, and participant responsibilities and commitments.\nb. Live-call kick-off\n4. 3 learning asynchronous learning modules via a digital collaboration platform for active real-time and asynchronous interactions among participants and program educators. This is the Eversheds Cohort Community on Slack (provided and moderated by Bold Duck Studio)\n5. An easy to access and use Digital Workbook for each participant that contains certain educational content and exercises.\n6. Two 2-hour live virtual working sessions with leading educators/experts in legal business design and strategy\na. First is focused on buildings skills and awareness within participants\nb. Second is focused on final team project presentations and analysis \n7. Post-program call with program stakeholders to assess impact and help Eversheds create a roadmap to build on the success of this experience.\nPractical tools and guides\nReal time collaboration in private digital platform\nBonus material:\nlaw firm finance foundational course\n\nKey outcomes include:\n\u2022 Enhancing your business literacy and insight ability in a manner clients can see and experience\n\u2022 Identifying with specificity and evidence areas of potential client value creation \n\u2022 Communicating insights in a credible and engaging manner in order to enroll support and action by partners and clients\n\u2022 The Client Value Design method is based on the proven research-based discipline of business design.\n\u2022 Business Design is a distinctive methodology for finding, framing and solving business challenges and opportunities for how legal services are delivered \u2013 from small law to BigLaw from in-house teams to legal aid. Business Design is the discipline that combines applied creativity and business rigor to unlock meaningful value for clients and measurable value for the lawyer. It draws upon social science, design, entrepreneurship, and strategy to create this business value - from innovative new products, services and processes to creative strategies and business models.\n\u2022 Below is an overview of the Client Value Design curriculum:\n\nLearning Objectives\nBy fully engaging and completing this program, each participant will be able to:\n1. Formulate an insight-driven expression of a client\u2019s business model to discover potential value-creation opportunities (legal and business) for Eversheds to prompt and potentially deliver on.\n2. Explain, define, and communicate a client\u2019s business model to client stakeholders, firm partners, and others. \n3. Apply key business model tools for insight and analysis that draw on both the discipline of design and of strategic analysis.\n4. Consistently and confidently rely on their fellow participants for go-forward support, collaboration, and skills advancement.\n5. Iterate and build upon the resources they can evolve and keep as part of this program.\n\nStatement of Work (SOW) for Executive Sprint Course:\n\nIntroduction\nThis SOW outlines the scope of work for an executive-level educational experience, which is tailored to the specific focus and needs of your firm. The program is designed to enhance your business literacy, provide you with tools to leverage your firm's resources and clients, and help you identify areas of potential client value creation.\n\nScope of Work\nThe scope of work includes the following components:\n\nPre-program design session with program stakeholders to gather requirements and define the program's focus.\nParticipant onboarding design, consisting of email invitations with program description, learning outcomes, and participant responsibilities and commitments, as well as a live-call kick-off.\nThree asynchronous learning modules via a digital collaboration platform for active real-time and asynchronous interactions among participants and program educators. This is the LAW FIRM Cohort Community on Slack (provided and moderated by Bold Duck Studio).\nAn easy-to-use digital workbook for each participant containing educational content and exercises.\nTwo 2-hour live virtual working sessions with leading educators/experts in legal business design and strategy. The first session will focus on building skills and awareness within participants, and the second session will focus on final team project presentations and analysis.\nPost-program call with program stakeholders to assess impact and help LAW FIRM create a roadmap to build on the success of this experience.\nAccess to practical tools and guides, real-time collaboration in a private digital platform, and bonus material: a law firm finance foundational course.\nDeliverables\nThe following deliverables will be provided:\nA comprehensive executive-level educational experience that is tailored to your firm's specific focus and needs.\nAccess to an online learning platform that includes three asynchronous learning modules, an easy-to-use digital workbook, and a private digital collaboration platform for real-time and asynchronous interactions among participants and program educators.\nTwo 2-hour live virtual working sessions with leading educators/experts in legal business design and strategy.\nPost-program call with program stakeholders to assess impact and create a roadmap to build on the success of the experience.\nPractical tools and guides to support continued learning and growth.\nRoles and Responsibilities\nThe professional services provider (Bold Duck Studio) is responsible for designing and delivering the executive-level educational experience, providing access to the online learning platform, and facilitating the two 2-hour live virtual working sessions.\nParticipants are responsible for completing the assigned learning modules, participating in the live virtual working sessions, and contributing to the private digital collaboration platform.\nProject Management\nBold Duck Studio will manage the project, including scheduling the live virtual working sessions, monitoring participant progress, and providing support and guidance throughout the program.\nChange Management\nAny changes to the scope of work must be agreed upon by both parties in writing.\nContractual Terms and Conditions\nThe contract will include the agreed-upon deliverables, timelines, and payment terms.\nAcceptance Criteria\nThe program will be considered complete when all learning modules have been completed, the two 2-hour live virtual working sessions have been attended, and the post-program call has been completed.\nConclusion\nThis SOW outlines the scope of work for an executive-level educational experience that is tailored to your firm's specific focus and needs. The program is designed to enhance your business literacy, provide you with tools to leverage your firm's resources and clients, and help you identify areas of potential client value creation.", "model": "Qwen/Qwen2.5-Coder-7B-Instruct", "prompt_length": 1382, "output_length": 731}]}



Before sending any requests, we should first check the number of pods in the cluster. It should be 1 at this time by using the following command.

.. code-block:: shell

    kubectl get pods

Then, we can forward the port of the gateway to the local machine by using the following command.

.. code-block:: shell

    kubectl -n envoy-gateway-system port-forward service/envoy-aibrix-system-aibrix-eg-903790dc 8888:80 &




Once the gateway is port-forwarded, we can send requests to the gateway using the following command by using the client.py script at /benchmarks/client/client.py




.. code-block:: shell

    python3 client.py \
    --workload-path "../generator/output/synthetic_manual_config.jsonl" \
    --endpoint "http://localhost:8888" \
    --model deepseek-llm-7b-chat  \
    --api-key "sk-kFJ12nKsFVfVmGpj3QzX65s4RbN2xJqWzPYCjYu7wT3BlbLi" \
    --streaming \
    --output-file-path output.jsonl 


You can watch the number of pods by running the following command and you should be able to see the number of pods increasing and decreasing based on the workload:

.. code-block:: shell

    watch -n 1 kubectl get pods