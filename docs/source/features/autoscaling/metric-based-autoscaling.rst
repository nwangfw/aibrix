.. _metric-based-autoscaling:

===========================
Metric-based Autoscaling
===========================


AIBrix Autoscaler includes various metric-based autoscaling components, allowing users to conveniently select the appropriate scaler. These options include the Knative-based Kubernetes Pod Autoscaler (KPA), the native Kubernetes Horizontal Pod Autoscaler (HPA), and AIBrix’s custom Advanced Pod Autoscaler (APA) tailored for LLM-serving.

In the following sections, we will demonstrate how users can create various types of autoscalers within AIBrix.


Supported Autoscaling Mechanism
-------------------------------

- HPA: it is same as vanilla K8s HPA. HPA, the native Kubernetes autoscaler, is utilized when users deploy a specification with AIBrix that calls for an HPA. This setup scales the replicas of a demo deployment based on CPU utilization.
- KPA: it is from Knative. KPA has panic mode which scales up more quickly based on short term history. More rapid scaling is possible. The KPA, inspired by Knative, maintains two time windows: a longer ``stable window`` and a shorter ``panic window``. It rapidly scales up resources in response to sudden spikes in traffic based on the panic window measurements. Unlike other solutions that might rely on Prometheus for gathering deployment metrics, AIBrix fetches and maintains metrics internally, enabling faster response times. Example of a KPA scaling operation using a mocked vllm-based Llama2-7b deployment
- APA: similar as HPA but it has fluctuation parameter which acts as minimum buffer before triggering scaling up and down to prevent oscillation.

While HPA and KPA are widely used, they are not specifically designed and optimized for LLM serving, which has distinct optimization points. AIBrix's custom APA (AIBrix Pod Autoscaler) solution will gradually introduce features such as:

- Selecting appropriate LLM-specific metrics for scaling based on AI Runtime metrics standardization.
- Proactive scaling algorithm rather than a reactive one. (WIP)
- Profiling & SLO driven autoscaling solution. (Testing Phase)


Metrics
-------

AiBrix supports all the vllm metrics. Please refer to https://docs.vllm.ai/en/stable/serving/metrics.html

How to deploy autoscaling policy
--------------------------------

It is simply applying PodAutoscaler yaml file.
One important thing you should note is that the deployment name and the name in `scaleTargetRef` in PodAutoscaler must be same.
That's how AiBrix PodAutoscaler refers to the right deployment.

All the sample files can be found in the following directory. 

.. code-block:: bash
    
    https://github.com/vllm-project/aibrix/tree/main/samples/autoscaling

Example HPA yaml config
^^^^^^^^^^^^^^^^^^^^^^^

.. literalinclude:: ../../../../samples/autoscaling/hpa.yaml
   :language: yaml

Example KPA yaml config
^^^^^^^^^^^^^^^^^^^^^^^

.. literalinclude:: ../../../../samples/autoscaling/kpa.yaml
   :language: yaml


Example APA yaml config
^^^^^^^^^^^^^^^^^^^^^^^

.. literalinclude:: ../../../../samples/autoscaling/apa.yaml
   :language: yaml


Check autoscaling logs
----------------------

Pod Autoscaler Logs
^^^^^^^^^^^^^^^^^^^

Pod autoscaler is part of aibrix controller manager which plays the role of collecting the metrics from each pod. You can
check its logs in this way.

.. code-block:: bash

    kubectl logs <aibrix-controller-manager-podname> -n aibrix-system -f

Expected log output. You can see the current metric is gpu_cache_usage_perc. You can check each pod's current metric value.

.. image:: ../../assets/images/autoscaler/aibrix-controller-manager-output.png
   :alt: AiBrix controller manager output
   :width: 100%
   :align: center


Custom Resource Status
^^^^^^^^^^^^^^^^^^^^^^

To describe the PodAutoscaler custom resource, you can run

.. code-block:: bash

    kubectl describe podautoscaler <podautoscaler-name>

Example output is here, you can explore the scaling conditions and events for more details.

.. image:: ../../assets/images/autoscaler/podautoscaler-describe.png
   :alt: PodAutoscaler describe
   :width: 100%
   :align: center


A Sample Autoscaling Test
----------------------------

Deploy PodAutoscaler
^^^^^^^^^^^^^^^^^^^^^^^

There are several different pod autoscaler strategies supported by AIBrix. In this demo, we will Knative Pod Autoscaler (KPA) as the pod autoscaler.

.. code-block:: shell

    kubectl apply -f benchmarks/autoscaling/deepseek-llm-7b-chat/kpa.yaml


Once the KPA is deployed, we can use the following command to check its status:

.. code-block:: shell

    kubectl get podautoscaler 


Dataset Downloading
^^^^^^^^^^^^^^^^^^^^^^^


In this test, we first download the public ShareGPT dataset, which will be used to generate our experiment workloads with ``workload_generator`` for different arrival patterns. 

.. code-block:: shell

    wget https://huggingface.co/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered/resolve/main/ShareGPT_V3_unfiltered_cleaned_split.json -O /tmp/ShareGPT_V3_unfiltered_cleaned_split.json


Then, we can generate a synthetic workload using the following command:

.. code-block:: shell

    export SHAREGPT_FILE_PATH=/tmp/ShareGPT_V3_unfiltered_cleaned_split.json
    python benchmarks/generator/workload_generator.py --prompt-file $SHAREGPT_FILE_PATH --interval-ms 1000 --duration-ms 300000 --trace-type synthetic --traffic-pattern "quick_rising" --prompt-len-pattern "slight_fluctuation" --completion-len-pattern "slight_fluctuation" --output-dir "./output" --output-format jsonl 

After running the above command, you will find the generated workload called "synthetic_manual_config.jsonl" in the ``output`` directory. The following are some samples from the dataset:

.. code-block:: shell

    {"timestamp": 536, "requests": [{"prompt": "It seems that you are running into an error because the option name \"maxSize\" is not a valid format. According to the Discord.js documentation, option names must be 1-32 characters long and can only contain lowercase letters, numbers, and symbols - and \\_.\n\nIn order to add a maxSize option to your /save command, you will need to change the name of the option to something that meets these constraints and then update the code in the execute function to check the maxSize and compare it with attachment size before saving the image.\n\nHere is an example of how you can update your code to include a maxSize option:\n```\nconst { SlashCommandBuilder } = require(\"discord.js\");\nconst fs = require(\"fs\");\nconst path = require(\"path\");\nconst https = require(\"https\");\nconst http = require(\"http\");\n\nmodule.exports = {\n  data: new SlashCommandBuilder()\n    .setName(\"save\")\n    .setDescription(\"Save the last X images in the channel.\")\n    .addIntegerOption((option) =>\n      option.setName(\"amount\").setDescription(\"Number of images to save\")\n    )\n    .addIntegerOption((option) =>\n      option\n        .setName(\"max_size\")\n        .setDescription(\"Maximum size of attachments in KB\")\n    ),\n\n  async execute(interaction) {\n    const amount = interaction.options.getInteger(\"amount\");\n    const maxSize = interaction.options.getInteger(\"max_size\") * 1000;\n\n    if (amount < 1 || amount > 99) {\n      return interaction.reply({\n        content: \"You need to input a number between 1 and 99.\",\n        ephemeral: true,\n      });\n    }\n\n    // Retrieve the last X images in the channel\n    const messages = await interaction.channel.messages.fetch({\n      limit: amount,\n    });\n    console.log(\"Retrieved messages:\", messages.size);\n    const images = messages.filter((message) => message.attachments.size > 0);\n    console.log(\"Retrieved images:\", images.size);\n\n    // Save the images to the local disk\n    const channelName = interaction.channel.name;\n    const dir = `./images/${\n      channelName ? channelName.replace(/[\\\\\\/:*?\"<>|]/g, \"\") : \"default\"\n    }`;\n    console.log(\"path: \" + dir);\n    if (!fs.existsSync(dir)) {\n      fs.mkdirSync(dir);\n      console.log(\"folder created: \" + dir);\n    }\n\n    let savedImagesCount = 0;\n    const allowedExtensions = [\".jpg\", \".jpeg\", \".png\", \".gif\"];\n\n    for (const [i, message] of images.entries()) {\n      for (const [j, attachment] of message.attachments.entries()) {\n        const fileName = attachment.name;\n        const extension = path.extname(fileName).toLowerCase();\n        if (!allowedExtensions.includes(extension)) {\n          console.log(\n            `Skipping file ${fileName} because it has an invalid extension.`\n          );\n          continue;\n        }\n        if (attachment.size > maxSize) {\n          console.log(\n            `Skipping file ${fileName\n```", "model": "Qwen/Qwen2.5-Coder-7B-Instruct", "prompt_length": 629, "output_length": 794}]}
    {"timestamp": 666, "requests": [{"prompt": "Can you draft an SOW based on the following partial information? If there are sections of a typical SOW that this information does not address, note them and leave them blank.\n\nThis executive sprint course is a combination of executive-level educational experiences tailored to your specific focus and needs. \n\nOur approach blends traditional workshopping, with curated education modules comprising of brief videos, reading content and exercises. All of this is combined to create milestone driven progress among participants ensuring your investment of time and energy sticks and lasts.\n\nWhat you\u2019ll get:\nThis 6-week dynamic learning experience is tailored to help you thrive. You will learn strategies and tools to leverage your firm\u2019s resources and clients so that new value creation opportunities can be discovered. You will learn and apply new design techniques and methods to find, frame, and communicate client value opportunities in contemporary and compelling ways. \n\nLearning Delivery\n1. Pre-program design session with program stakeholders\n2. Participant onboarding design, consisting of\na. Email invitations with program description, learning outcomes, and participant responsibilities and commitments.\nb. Live-call kick-off\n4. 3 learning asynchronous learning modules via a digital collaboration platform for active real-time and asynchronous interactions among participants and program educators. This is the Eversheds Cohort Community on Slack (provided and moderated by Bold Duck Studio)\n5. An easy to access and use Digital Workbook for each participant that contains certain educational content and exercises.\n6. Two 2-hour live virtual working sessions with leading educators/experts in legal business design and strategy\na. First is focused on buildings skills and awareness within participants\nb. Second is focused on final team project presentations and analysis \n7. Post-program call with program stakeholders to assess impact and help Eversheds create a roadmap to build on the success of this experience.\nPractical tools and guides\nReal time collaboration in private digital platform\nBonus material:\nlaw firm finance foundational course\n\nKey outcomes include:\n\u2022 Enhancing your business literacy and insight ability in a manner clients can see and experience\n\u2022 Identifying with specificity and evidence areas of potential client value creation \n\u2022 Communicating insights in a credible and engaging manner in order to enroll support and action by partners and clients\n\u2022 The Client Value Design method is based on the proven research-based discipline of business design.\n\u2022 Business Design is a distinctive methodology for finding, framing and solving business challenges and opportunities for how legal services are delivered \u2013 from small law to BigLaw from in-house teams to legal aid. Business Design is the discipline that combines applied creativity and business rigor to unlock meaningful value for clients and measurable value for the lawyer. It draws upon social science, design, entrepreneurship, and strategy to create this business value - from innovative new products, services and processes to creative strategies and business models.\n\u2022 Below is an overview of the Client Value Design curriculum:\n\nLearning Objectives\nBy fully engaging and completing this program, each participant will be able to:\n1. Formulate an insight-driven expression of a client\u2019s business model to discover potential value-creation opportunities (legal and business) for Eversheds to prompt and potentially deliver on.\n2. Explain, define, and communicate a client\u2019s business model to client stakeholders, firm partners, and others. \n3. Apply key business model tools for insight and analysis that draw on both the discipline of design and of strategic analysis.\n4. Consistently and confidently rely on their fellow participants for go-forward support, collaboration, and skills advancement.\n5. Iterate and build upon the resources they can evolve and keep as part of this program.\n\nStatement of Work (SOW) for Executive Sprint Course:\n\nIntroduction\nThis SOW outlines the scope of work for an executive-level educational experience, which is tailored to the specific focus and needs of your firm. The program is designed to enhance your business literacy, provide you with tools to leverage your firm's resources and clients, and help you identify areas of potential client value creation.\n\nScope of Work\nThe scope of work includes the following components:\n\nPre-program design session with program stakeholders to gather requirements and define the program's focus.\nParticipant onboarding design, consisting of email invitations with program description, learning outcomes, and participant responsibilities and commitments, as well as a live-call kick-off.\nThree asynchronous learning modules via a digital collaboration platform for active real-time and asynchronous interactions among participants and program educators. This is the LAW FIRM Cohort Community on Slack (provided and moderated by Bold Duck Studio).\nAn easy-to-use digital workbook for each participant containing educational content and exercises.\nTwo 2-hour live virtual working sessions with leading educators/experts in legal business design and strategy. The first session will focus on building skills and awareness within participants, and the second session will focus on final team project presentations and analysis.\nPost-program call with program stakeholders to assess impact and help LAW FIRM create a roadmap to build on the success of this experience.\nAccess to practical tools and guides, real-time collaboration in a private digital platform, and bonus material: a law firm finance foundational course.\nDeliverables\nThe following deliverables will be provided:\nA comprehensive executive-level educational experience that is tailored to your firm's specific focus and needs.\nAccess to an online learning platform that includes three asynchronous learning modules, an easy-to-use digital workbook, and a private digital collaboration platform for real-time and asynchronous interactions among participants and program educators.\nTwo 2-hour live virtual working sessions with leading educators/experts in legal business design and strategy.\nPost-program call with program stakeholders to assess impact and create a roadmap to build on the success of the experience.\nPractical tools and guides to support continued learning and growth.\nRoles and Responsibilities\nThe professional services provider (Bold Duck Studio) is responsible for designing and delivering the executive-level educational experience, providing access to the online learning platform, and facilitating the two 2-hour live virtual working sessions.\nParticipants are responsible for completing the assigned learning modules, participating in the live virtual working sessions, and contributing to the private digital collaboration platform.\nProject Management\nBold Duck Studio will manage the project, including scheduling the live virtual working sessions, monitoring participant progress, and providing support and guidance throughout the program.\nChange Management\nAny changes to the scope of work must be agreed upon by both parties in writing.\nContractual Terms and Conditions\nThe contract will include the agreed-upon deliverables, timelines, and payment terms.\nAcceptance Criteria\nThe program will be considered complete when all learning modules have been completed, the two 2-hour live virtual working sessions have been attended, and the post-program call has been completed.\nConclusion\nThis SOW outlines the scope of work for an executive-level educational experience that is tailored to your firm's specific focus and needs. The program is designed to enhance your business literacy, provide you with tools to leverage your firm's resources and clients, and help you identify areas of potential client value creation.", "model": "Qwen/Qwen2.5-Coder-7B-Instruct", "prompt_length": 1382, "output_length": 731}]}



Before sending any requests, we should first check the number of pods in the cluster. It should be the default number (e.g., 1) at this time by using the following command.

.. code-block:: shell

    kubectl get pods

Then, we can forward the port of the gateway to the local machine by using the following command.

.. code-block:: shell

    kubectl -n envoy-gateway-system port-forward service/envoy-aibrix-system-aibrix-eg-903790dc 8888:80 &




Once the gateway is port-forwarded, we can send requests to the gateway using the following command by using the client.py script




.. code-block:: shell

    python3  benchmarks/client/client.py \
    --workload-path "benchmarks/generator/output/synthetic_manual_config.jsonl" \
    --endpoint "http://localhost:8888" \
    --model deepseek-llm-7b-chat  \
    --api-key "<replace with your api key if configed>" \
    --streaming \
    --output-file-path output.jsonl 


You can watch the number of pods by running the following command and you should be able to see the number of pods increasing and decreasing based on the workload:

.. code-block:: shell

    watch -n 1 kubectl get pods




Preliminary experiments with different autoscalers
--------------------------------------------------

Here we show the preliminary experiment results to show how different autoscaling mechanism and configuration for autoscaler affect the performance(latency) and cost(compute cost).
In AiBrix, user can easily deploy different autoscaler by simply applying K8s yaml.

- Set up
    - Model: Deepseek 7B chatbot model
    - GPU type: V100
    - Max number of GPU: 8
- Target metric and value
    - Target metric: gpu_kv_cache_utilization
    - Target value: 50%
- Workload
    - The overall RPS trend starts with low RPS and goes up relatively fast until T=500 to evaluate how different autoscaler and config reacts to the rapid load increase. After that, it goes down to low RPS quickly to evaluate scaling down behavior and goes up again slowly.
        - Average RPS trend: 1 RPS -> 4 RPS -> 8 RPS -> 10 RPS -> 2 RPS -> 6 RPS
    - RPS can be found in the second subfigure.
- Performance
    - HPA has the highest latency since its slow reaction. KPA is the most reactive with panic mode. APA was running with small delay window to save cost. It does save cost but ends up having higher latency than KPA when it scales down too aggressively from T=700 to T=1000. 
- Cost
    - The fourth figure shows the relative accumulated compute cost over time. The accumulated cost is calculated by multiplying the time by unit cost (in this example, 1). The actual compute cost can be calculated by multiplying the actual cost per unit time.
    - HPA is the most expensive due to the longer delay window for scaling down.
    - APA is the most responsive and saves the cost most. You can see it fluctuating more than other two autoscalers.
    - Note that scaling down window is not inherent feature of each autoscaling mechanism. It is configurable variable. We use the default value for HPA (300s).
- Conclusion
    - There is no one autoscaler that outperforms others for all metrics (latency, cost). In addition, the results might depend on the workloads. Infrastructure should provide easy way to configure whichever autoscaling mechanism they want and should be easily configurable since different users have different preference. For example, one might prefer cost over performance or vice versa. 


.. image:: ../../assets/images/autoscaler/autoscaling_result.png
   :alt: result
   :width: 70%
   :align: center
