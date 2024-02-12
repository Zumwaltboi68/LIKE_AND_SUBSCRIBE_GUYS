import os
import random
import gradio as gr
import numpy as np
import PIL.Image
import torch
from typing import List
from diffusers.utils import numpy_to_pil
from diffusers import StableCascadeDecoderPipeline, StableCascadePriorPipeline
from diffusers.pipelines.wuerstchen import DEFAULT_STAGE_C_TIMESTEPS

#import user_history

os.environ['TOKENIZERS_PARALLELISM'] = 'false'

DESCRIPTION = "# Stable Cascade"
#DESCRIPTION += "\n<p style=\"text-align: center\"><a href='https://huggingface.co/warp-ai/wuerstchen' target='_blank'>Würstchen</a> is a new fast and efficient high resolution text-to-image architecture and model</p>"
if not torch.cuda.is_available():
    DESCRIPTION += "\n<p>Running on CPU 🥶</p>"

MAX_SEED = np.iinfo(np.int32).max
CACHE_EXAMPLES = torch.cuda.is_available() and os.getenv("CACHE_EXAMPLES") == "1"
MAX_IMAGE_SIZE = int(os.getenv("MAX_IMAGE_SIZE", "1536"))
USE_TORCH_COMPILE = True
ENABLE_CPU_OFFLOAD = os.getenv("ENABLE_CPU_OFFLOAD") == "1"
PREVIEW_IMAGES = False #not working for now

dtype = torch.float16
device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
if torch.cuda.is_available():
    prior_pipeline = StableCascadePriorPipeline.from_pretrained("diffusers/StableCascade-prior", torch_dtype=torch.bfloat16).to("cuda")
    decoder_pipeline = StableCascadeDecoderPipeline.from_pretrained("diffusers/StableCascade-decoder",  torch_dtype=torch.bfloat16).to("cuda") 

    if ENABLE_CPU_OFFLOAD:
        prior_pipeline.enable_model_cpu_offload()
        decoder_pipeline.enable_model_cpu_offload()
    else:
        prior_pipeline.to(device)
        decoder_pipeline.to(device)

    if USE_TORCH_COMPILE:
        prior_pipeline.prior = torch.compile(prior_pipeline.prior, mode="max-autotune", fullgraph=True)
        decoder_pipeline.decoder = torch.compile(decoder_pipeline.decoder, mode="max-autotune", fullgraph=True)
        prior_pipeline.fuse_qkv_projections()
        decoder_pipeline.fuse_qkv_projections()
        torch._inductor.config.conv_1x1_as_mm = True
        torch._inductor.config.coordinate_descent_tuning = True
        torch._inductor.config.epilogue_fusion = False
        torch._inductor.config.coordinate_descent_check_all_directions = True
        prior_pipeline.prior.to(memory_format=torch.channels_last)
        decoder_pipeline.decoder.to(memory_format=torch.channels_last)
    if PREVIEW_IMAGES:
        pass
    #    previewer = Previewer()
    #    previewer.load_state_dict(torch.load("previewer/text2img_wurstchen_b_v1_previewer_100k.pt")["state_dict"])
    #    previewer.eval().requires_grad_(False).to(device).to(dtype)

    #    def callback_prior(i, t, latents):
    #        output = previewer(latents)
    #        output = numpy_to_pil(output.clamp(0, 1).permute(0, 2, 3, 1).cpu().numpy())
    #        return output

    else:
        previewer = None
        callback_prior = None
else:
    prior_pipeline = None
    decoder_pipeline = None


def randomize_seed_fn(seed: int, randomize_seed: bool) -> int:
    if randomize_seed:
        seed = random.randint(0, MAX_SEED)
    return seed


def generate(
    prompt: str,
    negative_prompt: str = "",
    seed: int = 0,
    width: int = 1024,
    height: int = 1024,
    prior_num_inference_steps: int = 60,
    # prior_timesteps: List[float] = None,
    prior_guidance_scale: float = 4.0,
    decoder_num_inference_steps: int = 12,
    # decoder_timesteps: List[float] = None,
    decoder_guidance_scale: float = 0.0,
    num_images_per_prompt: int = 2,
    #profile: gr.OAuthProfile | None = None,
) -> PIL.Image.Image:
    generator = torch.Generator().manual_seed(seed)

    prior_output = prior_pipeline(
        prompt=prompt,
        height=height,
        width=width,
        timesteps=DEFAULT_STAGE_C_TIMESTEPS,
        negative_prompt=negative_prompt,
        guidance_scale=prior_guidance_scale,
        num_images_per_prompt=num_images_per_prompt,
        generator=generator,
        callback=callback_prior,
    )

    #if PREVIEW_IMAGES:
    #    for _ in range(len(DEFAULT_STAGE_C_TIMESTEPS)):
    #        r = next(prior_output)
    #        if isinstance(r, list):
    #            yield r
    #    prior_output = r

    decoder_output = decoder_pipeline(
        image_embeddings=prior_output.image_embeddings,
        prompt=prompt,
        num_inference_steps=decoder_num_inference_steps,
        # timesteps=decoder_timesteps,
        guidance_scale=decoder_guidance_scale,
        negative_prompt=negative_prompt,
        generator=generator,
        output_type="pil",
    ).images

    # Save images
    #for image in decoder_output:
    #    user_history.save_image(
    #        profile=profile,
    #        image=image,
    #        label=prompt,
    #        metadata={
    #            "negative_prompt": negative_prompt,
    #            "seed": seed,
    #            "width": width,
    #            "height": height,
    #            "prior_guidance_scale": prior_guidance_scale,
    #            "decoder_num_inference_steps": decoder_num_inference_steps,
    #            "decoder_guidance_scale": decoder_guidance_scale,
    #            "num_images_per_prompt": num_images_per_prompt,
    #        },
    #    )

    yield decoder_output


examples = [
    "Astronaut in a jungle, cold color palette, muted colors, detailed, 8k",
    "An astronaut riding a green horse",
]

with gr.Blocks() as demo:
    gr.Markdown(DESCRIPTION)
    gr.DuplicateButton(
        value="Duplicate Space for private use",
        elem_id="duplicate-button",
        visible=os.getenv("SHOW_DUPLICATE_BUTTON") == "1",
    )
    with gr.Group():
        with gr.Row():
            prompt = gr.Text(
                label="Prompt",
                show_label=False,
                max_lines=1,
                placeholder="Enter your prompt",
                container=False,
            )
            run_button = gr.Button("Run", scale=0)
        result = gr.Gallery(label="Result", show_label=False)
    with gr.Accordion("Advanced options", open=False):
        negative_prompt = gr.Text(
            label="Negative prompt",
            max_lines=1,
            placeholder="Enter a Negative Prompt",
        )

        seed = gr.Slider(
            label="Seed",
            minimum=0,
            maximum=MAX_SEED,
            step=1,
            value=0,
        )
        randomize_seed = gr.Checkbox(label="Randomize seed", value=True)
        with gr.Row():
            width = gr.Slider(
                label="Width",
                minimum=1024,
                maximum=MAX_IMAGE_SIZE,
                step=512,
                value=1024,
            )
            height = gr.Slider(
                label="Height",
                minimum=1024,
                maximum=MAX_IMAGE_SIZE,
                step=512,
                value=1024,
            )
            num_images_per_prompt = gr.Slider(
                label="Number of Images",
                minimum=1,
                maximum=2,
                step=1,
                value=2,
            )
        with gr.Row():
            prior_guidance_scale = gr.Slider(
                label="Prior Guidance Scale",
                minimum=0,
                maximum=20,
                step=0.1,
                value=4.0,
            )
            prior_num_inference_steps = gr.Slider(
                label="Prior Inference Steps",
                minimum=10,
                maximum=30,
                step=1,
                value=25,
            )

            decoder_guidance_scale = gr.Slider(
                label="Decoder Guidance Scale",
                minimum=0,
                maximum=0,
                step=0.1,
                value=0.0,
            )
            decoder_num_inference_steps = gr.Slider(
                label="Decoder Inference Steps",
                minimum=4,
                maximum=12,
                step=1,
                value=10,
            )

    gr.Examples(
        examples=examples,
        inputs=prompt,
        outputs=result,
        fn=generate,
        cache_examples=CACHE_EXAMPLES,
    )

    inputs = [
            prompt,
            negative_prompt,
            seed,
            width,
            height,
            prior_num_inference_steps,
            # prior_timesteps,
            prior_guidance_scale,
            decoder_num_inference_steps,
            # decoder_timesteps,
            decoder_guidance_scale,
            num_images_per_prompt,
    ]
    gr.on(
        triggers=[prompt.submit, negative_prompt.submit, run_button.click],
        fn=randomize_seed_fn,
        inputs=[seed, randomize_seed],
        outputs=seed,
        queue=False,
        api_name=False,
    ).then(
        fn=generate,
        inputs=inputs,
        outputs=result,
        api_name="run",
    )
    
with gr.Blocks(css="style.css") as demo_with_history:
    #with gr.Tab("App"):
    demo.render()
    #with gr.Tab("Past generations"):
    #    user_history.render()

if __name__ == "__main__":
    demo_with_history.queue(max_size=20).launch()