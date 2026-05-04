import torch
from PIL import Image
import open_clip


class TruckViewClassifier:
    def __init__(
        self,
        model_name: str = "ViT-B-32",
        pretrained: str = "laion2b_s34b_b79k",
        device: str = None,
    ):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")

        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            model_name,
            pretrained=pretrained,
        )
        self.model = self.model.to(self.device)
        self.model.eval()

        self.tokenizer = open_clip.get_tokenizer(model_name)
        self.orientation_prompts = [
            ("a photo of a truck where the front cab is on the left side of the image", "cab_on_left"),
            ("a truck with its front facing left side of the image", "cab_on_left"),
            ("truck side view with cabin on left side", "cab_on_left"),
            ("truck engine or side where cab appears on left side of image", "cab_on_left"),
            ("a photo of a truck where the front cab is on the right side of the image", "cab_on_right"),
            ("a truck with its front facing right side of the image", "cab_on_right"),
            ("truck side view with cabin on right side", "cab_on_right"),
            ("truck engine or side where cab appears on right side of image", "cab_on_right"),
        ]
        self.orientation_tokens = self._tokenize_prompts(self.orientation_prompts)

    def _tokenize_prompts(self, prompt_label_pairs):
        prompts = [prompt for prompt, _ in prompt_label_pairs]
        return self.tokenizer(prompts).to(self.device)

    def _load_image(self, image_input):
        if isinstance(image_input, Image.Image):
            return image_input.convert("RGB")
        return Image.open(image_input).convert("RGB")

    def _predict_orientation(self, image):
        image_tensor = self.preprocess(image).unsqueeze(0).to(self.device)
        flipped_tensor = self.preprocess(image.transpose(Image.FLIP_LEFT_RIGHT)).unsqueeze(0).to(self.device)

        with torch.no_grad():
            image_features = self.model.encode_image(image_tensor).to(torch.float32)
            flipped_features = self.model.encode_image(flipped_tensor).to(torch.float32)
            text_features = self.model.encode_text(self.orientation_tokens).to(torch.float32)

            image_features /= image_features.norm(dim=-1, keepdim=True)
            flipped_features /= flipped_features.norm(dim=-1, keepdim=True)
            text_features /= text_features.norm(dim=-1, keepdim=True)

            sims = (image_features @ text_features.T).cpu().numpy()[0]
            flipped_sims = (flipped_features @ text_features.T).cpu().numpy()[0]

        left_scores = sims[:4]
        right_scores = sims[4:]

        flipped_left = flipped_sims[:4]
        flipped_right = flipped_sims[4:]

        # KEY IDEA: compare how scores change after flipping
        left_diff = (left_scores.mean() - flipped_left.mean())
        right_diff = (right_scores.mean() - flipped_right.mean())

        if left_diff > right_diff:
            label = "cab_on_left"
            confidence = float(left_diff)
        else:
            label = "cab_on_right"
            confidence = float(right_diff)

        return {
            "predicted_label": label,
            "confidence": confidence,
            "debug": {
                "left_diff": float(left_diff),
                "right_diff": float(right_diff),
            }
        }

    def classify_image(self, image_input):
        """Predict whether the truck cab appears on the left or right side of the image."""
        image = self._load_image(image_input)
        orientation_result = self._predict_orientation(image)

        flipped_image = image.transpose(Image.FLIP_LEFT_RIGHT)
        flipped_result = self._predict_orientation(flipped_image)
        expected_flipped_label = (
            "cab_on_right"
            if orientation_result["predicted_label"] == "cab_on_left"
            else "cab_on_left"
        )
        orientation_result["flip_test"] = {
            "flipped_prediction": flipped_result["predicted_label"],
            "consistent": flipped_result["predicted_label"] == expected_flipped_label,
        }

        return orientation_result
